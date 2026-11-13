#!/usr/bin/env python3
"""Turn the raw OCN pull (context/ocn/raw/articles/) into signal.v1.1 rows. Standard library only.

  python scripts/build_signals.py            # writes context/ocn/signals/
  python scripts/build_signals.py --selftest

Dedup follows OCN's news-retrieval, in the same order:
  1. URL: one row per canonical URL; the same story filed under two OCN domains becomes one row.
  2. Headline near-duplicates, per OCN domain, inside OCN's time window and similarity threshold for
     that domain. The dropped copy is recorded on the kept row in also_reported_by as {outlet, title, url}.
  3. cluster_id: rows from different domains about the same story share a cluster, so the event
     counts once. The first to report it gets novelty 1, later ones 0.
Similarity is the cosine of OCN's own title embeddings (text-embedding-3-small) when both rows carry one.

Links to Anthropic are recomputed here from context/markets/links.csv on every build, so editing that
file needs a rebuild, not a new scan. Rule-based labels (signal_detection, category) stand in until
OCN's classifier grades these rows; each rule names itself in `evidence`.
"""
import argparse, csv, difflib, gzip, json, math, re, sys, urllib.parse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "context/ocn/raw/articles", ROOT / "context/ocn/signals"
SCHEMA_VERSION = 1  # major; this pack is v1.1 (additive: signal_detection, category, matched_in_title, link_type in the impact key)

# Copied from OCN news-retrieval/src/pipeline.py (_TITLE_DEDUP_DOMAINS and the per-domain maps).
WINDOW_HOURS = {"ai_news": 48, "smart_money": 48, "geopolitical_news": 168, "korea_market_signal": 24,
                "middle_east_market_signal": 168, "japan_market_signal": 24}
THRESHOLD = {"geopolitical_news": 0.82, "middle_east_market_signal": 0.82, "japan_market_signal": 0.95}
DEFAULT_THRESHOLD = 0.90
EXCLUDED_CATEGORIES = {"korea_market_signal": {"dart_filing", "kr_customs_export"},
                       "middle_east_market_signal": {"me_official_wire", "me_us_policy", "me_fund_stake"},
                       "japan_market_signal": {"jp_forecast", "jp_results", "jp_buyback", "jp_buyback_status", "jp_buyback_announce",
                                               "jp_company_reference", "jp_disclosure", "jp_extraordinary", "jp_ownership",
                                               "jp_industry", "jp_capex", "jp_english_coverage_check"}}
SAME_DAY_TZ = {"japan_market_signal": timezone(timedelta(hours=9))}
# ponytail: OCN embeds a title when it has none; we have no embedding key, so a pair without two stored
# embeddings is compared by text instead, at a stricter near-verbatim bar. Embed missing titles to match OCN exactly.
TEXT_THRESHOLD = 0.90
CLUSTER_WINDOW_HOURS = 48

REGION = {"taiwan_market_signal": "TW", "korea_market_signal": "KR", "japan_market_signal": "JP",
          "china_market_signal": "CN", "middle_east_market_signal": "ME"}
# OCN source_category -> signal.v1 source_kind, for the rows that are not plain news
FILING_CATEGORIES = {"dart_filing", "kr_quarterly_results", "cn_disclosure", "jp_disclosure"}
STATE_CATEGORIES = {"kr_quarterly_results"}  # a quarter's results hold until the next quarter's
OFFICIAL_PRESS = {"cn_policy", "me_official_wire"}
# Starting priors from signal.v1 (Overall-plan.md); calibration replaces them.
RELIABILITY = [(0.8, r"reuters|bloomberg|associated press|apnews|dow jones|marketwatch"),
               (0.7, r"financial times|ft\.com|wall street journal|wsj|new york times|nytimes|cnbc|guardian|techcrunch|the verge|"
                     r"mit technology review|the information|axios|fortune|business insider|forbes|wired|ars technica|semafor|economist")]
LINK_CONFIDENCE = {"supplier": 0.8, "competitor": 0.6, "customer": 0.5, "peer": 0.4, "sector": 0.4}
AV_DIRECTION = {"Bullish": 1, "Somewhat-Bullish": 1, "Neutral": 0, "Somewhat-Bearish": -1, "Bearish": -1}

LINKS = list(csv.DictReader(open(ROOT / "context/markets/links.csv")))
for _l in LINKS:
    _l["re"], _l["types"] = re.compile(_l["title_pattern"]), _l["link_type"].split()
LINK_BY_ID = {l["issuer_id"]: l for l in LINKS}

# ponytail: headline rules until OCN's classifier grades these rows. Each is (label, rule name, pattern).
NOISE = [
    ("holdings", r"\b(?:purchases|acquires|buys|sells|trims|grows|lowers|boosts|reduces|increases|raises|cuts|takes)\b (?:its |a )?(?:new )?(?:[\d,.]+ shares|stake|position|holdings)\b.{0,40}\b(?:of|in)\b.*(?:\$[A-Z]{1,5}|Inc\.|Corp|Ltd|N\.V\.|S\.A\.)|\b(?:grows|trims|lowers|boosts|cuts|reduces|increases|raises) (?:its )?(?:stake|position|holdings)\s*$"),
    ("insider_filing", r"\bform 4\b|\binsider (?:trades|buying|selling|sells|buys)\b|transfers .* shares for no consideration"),
    ("quote_page", r"stock price, news, quote|balance sheet\s*[–-]|\bdepositary receipts?\b|^[^:]{3,60}\s\([A-Z]{1,5}\)\s*$"),
    ("price_move_template", r"^if you (?:had )?invested|would have this much|outperforms competitors|underperforms competitors|what'?s next\?\s*$|^what is going on with .{1,40} stock|^why (?:is|did) .{1,40} stock|moved (?:up|down) by [\d.]+%|\d+ straight (?:green|red) days|\bstock (?:falls|rises|drops|jumps|gains|slides|is up|is down) [\d.]+%|among (?:notable names|market cap stock movers)|stock movers on|top company headlines|stocks to watch"),
    ("legal_ad", r"shareholder alert|class action|investors? (?:who lost|with losses)|reminds investors|law firm|deadline alert|securities fraud"),
    ("dividend_fund", r"ex-dividend|declares (?:quarterly )?dividend|weeklypay|\betf\b.*\b(?:holdings|dividend)\b|\(\w+\) holdings\s*$"),
    ("event_notice", r"conference call to (?:review|discuss)|announces (?:date of|conference call)|to (?:announce|report) .{0,60} results on\b|to present at .{0,40} conference"),
]
NOISE = [(name, re.compile(rx, re.I)) for name, rx in NOISE]
# a price move with its cause in the headline is news, not a template ("jumps 31% on earnings beat")
MOVE_WITH_REASON = re.compile(r"\b(?:earnings|beat|miss|guidance|results|deal|acquisition|contract|downgrade|upgrade|lawsuit|ipo)\b", re.I)

# Topic categories. Half-life priors follow signal.v1: days (2) for momentum, pricing, competition, demand;
# weeks (10) for supply, geopolitics and deal events; quarters (30) for regulation and export controls.
CATEGORIES = [
    ("ipo", 10, r"\bipo\b|\bs-1\b|roadshow|\blisting\b|lock-?up|underwrit|\bdebut\b|price range|go(?:es|ing)? public"),
    ("funding", 10, r"\braises?\b.{0,30}\$|\bfunding\b|\bvaluation\b|valued at|\bround\b|\binvest(?:s|ment|ing)\b|private placement|tender offer"),
    ("earnings", 4, r"\bearnings\b|\bquarter(?:ly)?\b|\bq[1-4]\b|\bresults\b|\bguidance\b|\boutlook\b|\bprofit\b|\brevenue\b"),
    ("pricing", 2, r"\bprice (?:cut|hike|war)|\bpricing\b|per token|\bsubscription\b|\bcheaper\b|\bdiscount"),
    ("product", 4, r"\blaunch(?:es|ed)?\b|\breleas(?:es|ed)\b|\bunveil|\bintroduc|\bnew model\b|\bagents?\b|\bfeature\b|\bbeta\b|\bupgrade"),
    ("demand", 2, r"\bcustomers?\b|\badoption\b|\bcontract\b|\bdeal\b|\bpartnership\b|\bpartners? with\b|\bsigns?\b|\bbookings\b|\borders?\b|\benterprise\b"),
    ("supply", 10, r"\bchips?\b|\bgpus?\b|\bhbm\b|\bcapacity\b|data ?cent(?:er|re)s?\b|\bpower\b|\bppa\b|\bcompute\b|\bfabs?\b|\bshortage\b|\bmemory\b|\bnuclear\b|\bmegawatts?\b|\b\d+ ?[gm]w\b|\bcapex\b"),
    ("competition", 2, r"\brivals?\b|\bcompetit|\bmarket share\b|\bovertakes?\b|\bbeats?\b|\bvs\.?\b|\bversus\b|\bchallenger?\b"),
    ("talent", 4, r"\bhires?\b|\bhired\b|\bceo\b|\bexecutives?\b|\bdeparts?\b|\bresigns?\b|\bpoach|\bjoins\b|\blayoffs?\b"),
    ("legal", 30, r"\blawsuit\b|\bsues?\b|\bsued\b|\bcourt\b|\bjudge\b|\bcopyright\b|\bsettle(?:s|ment)\b|\bantitrust\b|\bftc\b|\bdoj\b"),
    ("regulation", 30, r"\bregulat|\brules\b|\blaw\b|\bbill\b|\bai act\b|\bpolicy\b|\bcongress\b|\bsenate\b|\bwhite house\b|\bexecutive order\b"),
    ("export_controls", 30, r"export control|export ban|entity list|chip ban|\bexport rules?\b"),
    ("geopolitics", 10, r"\btariffs?\b|\bsanctions?\b|\bchina\b|\btaiwan\b|\bwar\b|\belection\b|\bmidterms?\b|\bpentagon\b|\bdefen[cs]e\b"),
    ("momentum", 2, r"\bstocks?\b|\bshares\b|\brally\b|\bslides?\b|\bjumps?\b|\bfalls?\b|\brises?\b|\bsurges?\b|\bplunges?\b|\bsell-?off\b|\bsoars?\b|\btumbles?\b"),
]
CATEGORIES = [(name, hl, re.compile(rx, re.I)) for name, hl, rx in CATEGORIES]

REGIONAL_TITLE = re.compile(r"^(?P<co>[^\n]{1,60}?) \((?P<code>[\w.]+)\)(?: \[(?P<tag>[^\]]+)\])?: (?P<rest>.+)$", re.S)
HANGUL, KANA, HAN = re.compile(r"[가-힯]"), re.compile(r"[぀-ヿ]"), re.compile(r"[㐀-鿿]")


def ts(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def canonical_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not re.match(r"utm_|syn-|ref$|ref_|fbclid|gclid|guccounter", k)]
    host = p.netloc.lower().removeprefix("www.")
    return urllib.parse.urlunsplit((p.scheme.lower(), host, p.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def outlet(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def norm_title(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (t or "").lower()).split())


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) if v else 0
    return [x / n for x in v] if n else None


def display_title(raw: str, category: str | None) -> str:
    """Drop OCN's regional prefix: 'SK Hynix (000660) [English coverage check]: headline' -> 'headline';
    filings and regional press keep the company name: 'SK Hynix: report name'."""
    raw = " ".join((raw or "").split())
    m = REGIONAL_TITLE.match(raw)
    if not m:
        return raw
    return m["rest"] if (m["tag"] or "").endswith("coverage check") else f"{m['co']}: {m['rest']}"


def language(title: str, meta_lang: str | None) -> str:
    if HANGUL.search(title):
        return "ko"
    if KANA.search(title):
        return "ja"
    if HAN.search(title):
        return "zh"
    return (meta_lang or "en")[:2] if meta_lang not in ("eng",) else "en"


def links_for(raw_title: str) -> list[dict]:
    """Companies the headline names (OCN's regional prefix included: it names the company the row is about)."""
    return [{"issuer_id": l["issuer_id"], "types": l["types"]} for l in LINKS if l["re"].search(raw_title)]


def similar(a: dict, b: dict) -> tuple[float, bool]:
    """(similarity, from_embeddings): cosine of stored embeddings, else a text ratio judged against TEXT_THRESHOLD."""
    if a["_emb"] and b["_emb"]:
        return sum(x * y for x, y in zip(a["_emb"], b["_emb"])), True
    sm = difflib.SequenceMatcher(None, a["_norm"], b["_norm"])
    if sm.real_quick_ratio() < TEXT_THRESHOLD or sm.quick_ratio() < TEXT_THRESHOLD:
        return 0.0, False
    return sm.ratio(), False


def is_dup(a: dict, b: dict, threshold: float) -> bool:
    s, emb = similar(a, b)
    return s >= (threshold if emb else TEXT_THRESHOLD)


def prepare(r: dict) -> dict | None:
    m = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
    r["metadata"], cat = m, m.get("source_category")
    r["_raw_title"] = " ".join((r.get("title") or "").split())
    r["_title"] = display_title(r["_raw_title"], cat)
    r["_match"] = r.get("_match") or []
    r["_links"] = links_for(r["_raw_title"])
    if not (r["_match"] or r["_links"]) or not r["_title"]:
        return None
    r["_ant_in_title"] = any(t in r["_raw_title"].lower() for t in r["_match"])
    r["_url"] = m.get("article_url") if str(r.get("url", "")).startswith(("kr-english-check://", "cn-english-check://")) else r.get("url")
    r["_url"] = r["_url"] or r.get("url") or ""
    r["_pub"], r["_seen"] = ts(r.get("published") or ""), ts(r.get("created_at") or "")
    r["_t"] = r["_pub"] or r["_seen"]
    r["_emb"], r["_norm"] = unit(m.get("title_embedding")), norm_title(r["_title"])
    r["_domains"], r["_also"] = [r["domain"]], list(m.get("also_reported_by") or [])
    return r if r["_t"] else None


def load(raw: Path) -> list[dict]:
    rows = []
    for f in sorted(raw.glob("*.jsonl.gz")):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            if r := prepare(json.loads(line)):
                rows.append(r)
    return rows


def merge_into(a: dict, r: dict) -> None:
    a["_match"] = sorted(set(a["_match"]) | set(r["_match"]))
    a["_ant_in_title"] = a["_ant_in_title"] or r["_ant_in_title"]
    a["_links"] = list({l["issuer_id"]: l for l in a["_links"] + r["_links"]}.values())


def dedup_url(rows: list[dict]) -> list[dict]:
    kept: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["_seen"] or r["_t"]):
        k = canonical_url(r["_url"])
        if k not in kept:
            kept[k] = r
            continue
        a = kept[k]
        a["_domains"] = sorted(set(a["_domains"]) | {r["domain"]})
        merge_into(a, r)
        a["_also"] += [e for e in r["_also"] if e not in a["_also"]]
        a["_emb"] = a["_emb"] or r["_emb"]
        if not a["metadata"].get("ticker_sentiment"):
            a["metadata"]["ticker_sentiment"] = r["metadata"].get("ticker_sentiment")
    return list(kept.values())


def dedup_titles(rows: list[dict]) -> tuple[list[dict], int]:
    """OCN's _dedup_by_title_similarity_for_domain, run over the whole history in publish order."""
    out, dropped, by_domain = [], 0, defaultdict(list)
    for r in rows:
        by_domain[r["domain"]].append(r)
    for domain, items in by_domain.items():
        hours = WINDOW_HOURS.get(domain)
        excluded = EXCLUDED_CATEGORIES.get(domain, set())
        if not hours:
            out += items
            continue
        threshold, tz = THRESHOLD.get(domain, DEFAULT_THRESHOLD), SAME_DAY_TZ.get(domain)
        kept: list[dict] = []
        for r in sorted(items, key=lambda r: r["_t"]):
            if r["metadata"].get("source_category") in excluded:
                out.append(r)
                continue
            lo = r["_t"] - timedelta(hours=hours)
            match = next((k for k in reversed(kept) if k["_t"] >= lo
                          and (not tz or k["_t"].astimezone(tz).date() == r["_t"].astimezone(tz).date())
                          and is_dup(r, k, threshold)), None)
            if match is None:
                kept.append(r)
                continue
            entry = {"outlet": outlet(r["_url"]), "title": r["_title"][:300], "url": r["_url"]}
            if entry["outlet"] and entry["url"] not in {e.get("url") for e in match["_also"] if isinstance(e, dict)}:
                match["_also"].append(entry)
            merge_into(match, r)
            dropped += 1
        out += kept
    return out, dropped


def cluster(rows: list[dict]) -> None:
    """Same story across OCN domains -> one cluster_id (the earliest row). Nothing is dropped."""
    rows.sort(key=lambda r: r["_t"])
    times = [r["_t"] for r in rows]
    words = [set(r["_norm"].split()) for r in rows]
    for i, r in enumerate(rows):
        r["_cluster"] = f"ocn:{r['id']}"
        for j in range(bisect_left(times, r["_t"] - timedelta(hours=CLUSTER_WINDOW_HOURS)), i):
            o = rows[j]
            if set(o["_domains"]) & set(r["_domains"]) & WINDOW_HOURS.keys():
                continue  # same-domain pairs in OCN's title-dedup domains were already judged by dedup_titles
            w = words[i] | words[j]
            if w and len(words[i] & words[j]) / len(w) >= 0.2 and is_dup(r, o, DEFAULT_THRESHOLD):
                r["_cluster"] = o["_cluster"]
                break


def detect(r: dict) -> tuple[str, str]:
    """(signal_detection, evidence). Noise by headline template; weak when Anthropic is only in the body
    of a story whose headline names no linked company, or the only link is a sector one."""
    if not r["_ant_in_title"]:
        for name, rx in NOISE:
            if rx.search(r["_title"]) and not (name == "price_move_template" and MOVE_WITH_REASON.search(r["_title"])):
                return "noise", f"rule: {name}"
    if r["_match"] and not r["_ant_in_title"] and not r["_links"]:
        return "weak_signal", "rule: Anthropic named only in the body"
    if not r["_match"] and all(t in ("sector", "peer") for l in r["_links"] for t in l["types"]):
        return "weak_signal", "rule: only peer or sector companies named"
    return "signal", "rule: Anthropic or a linked company named in the headline"


def categories(r: dict) -> list[str]:
    text = f"{r['_title']} {r.get('summary') or ''}"[:800]
    return [name for name, _, rx in CATEGORIES if rx.search(text)]


RULES_VERSION = "build_signals-rules-2026-11-13"  # bump when NOISE, CATEGORIES or detect() change
NEWS_GRADES: dict[str, dict] = {}  # OCN article id -> OCN's AI-news grade, filled by build()


def load_news_grades(raw: Path) -> dict[str, dict]:
    """OCN's graded AI-news rows for our articles; the latest grade wins when OCN re-graded an article."""
    f, out = raw / "news.jsonl.gz", {}
    if f.exists():
        for line in gzip.open(f, "rt", encoding="utf-8"):
            g = json.loads(line)
            k = str(g.get("article_id"))
            if k not in out or (g.get("stored_at") or "") > (out[k].get("stored_at") or ""):
                out[k] = g
    return out


def apply_news_grade(s: dict, r: dict) -> None:
    """OCN's own classifier graded this article: its label, score, confidence, materiality and novelty replace the rules."""
    g = NEWS_GRADES.get(str(r["id"]))
    if not g:
        s["model"], s["prompt_version"] = "rules", RULES_VERSION
        return
    label = g["signal_detection"]
    if label == "noise" and r["_ant_in_title"]:
        label = "weak_signal"  # Anthropic in the headline always bears on the IPO
    s["signal_detection"], s["evidence"] = label, g.get("signal_reason") or s["evidence"]
    s["strength"] = float(g["signal_score"]) if g.get("signal_score") is not None else None
    s["confidence"], s["materiality"] = LEVEL.get(g.get("confidence")), LEVEL.get(g.get("materiality"))
    if g.get("novelty") in NOVELTY:
        s["novelty"] = NOVELTY[g["novelty"]]
    s["category"] = sorted(set(s["category"]) | set(g.get("category") or []))
    s["producer"] = "ocn/signal-detection-agent"
    s["_ocn"]["graded_result_id"] = g.get("id")


# ponytail: linear scales to 0-1 with a cap; the divisors are judgement until calibration measures each family
def family_strength(det: str, m: dict) -> float | None:
    def cap(x, d):
        try:
            return round(min(1.0, abs(float(x)) / d), 3)
        except (TypeError, ValueError):
            return None
    if det == "macro":
        z = m.get("z_scores") or m.get("z_score")
        if isinstance(z, dict):
            z = max((abs(v) for v in z.values() if isinstance(v, (int, float))), default=None)
        if z is not None:
            return cap(z, 4)  # |z| of 4 or more is full strength
        if m.get("percentile") is not None:
            return cap(float(m["percentile"]) - 50, 50)  # distance from the median of its 20-year range
        return None
    if det == "china":
        return cap(m.get("deviation_mads"), 10) if m.get("deviation_mads") is not None else cap(m.get("capex_change_pct"), 100)
    if det == "taiwan":
        return cap(m.get("revenue_deviation"), 3)
    if det == "korea":
        return cap(m.get("pct_of_revenue"), 30)
    if det == "japan":
        return cap(m.get("operating_profit_pct_change") if m.get("operating_profit_pct_change") is not None else m.get("revenue_pct_change"), 50)
    return None


def signal(r: dict) -> dict:
    url, m = r["_url"], r["metadata"]
    cat = m.get("source_category")
    source = r.get("source") or outlet(url)
    long_form = "substack.com" in url or m.get("source_type") == "substack_post" or re.search(r"newsletter|substack", source, re.I)
    kind = "exchange_disclosure" if cat in FILING_CATEGORIES else "long_form" if long_form else "news_article"
    hay = f"{source} {outlet(url)}".lower()
    reliability = (0.9 if kind == "exchange_disclosure" else 0.8 if cat in OFFICIAL_PRESS else 0.5 if long_form
                   else next((p for p, rx in RELIABILITY if re.search(rx, hay)), 0.6))
    in_title = [l["issuer_id"] for l in r["_links"]]
    subject = "ANT" if r["_match"] and (r["_ant_in_title"] or not in_title) else (in_title[0] if len(in_title) == 1 else None)
    cats = categories(r)
    hl = max((h for n, h, _ in CATEGORIES if n in cats), default=None)
    if hl is None:
        hl = 25 if kind == "long_form" else 15 if r["domain"] in ("geopolitical_news", "middle_east_market_signal") else 4
    label, evidence = detect(r)
    lang = language(r["_title"], m.get("language"))
    summary = r.get("summary") if r.get("summary") not in (None, "None", "") else None
    pub, seen = r["_pub"], r["_seen"]
    region = REGION.get(r["domain"], "global")
    return {
        "signal_id": f"ocn:{r['id']}", "schema_version": SCHEMA_VERSION,
        "source_kind": kind, "source_name": source, "source_url": url,
        "source_reliability": reliability,
        "scope": "company" if subject else "sector",
        "detector": "news", "subtype": r["domain"],
        "signal_detection": label, "category": cats,
        # ponytail: no translation key here; non-English titles stay as they are and are copied to title_original
        "title": r["_title"], "title_original": r["_title"] if lang != "en" else None, "language": lang,
        "summary": summary[:600] if summary else None,
        "region": region, "country": region if region in ("TW", "KR", "JP", "CN") else None,
        "subject_issuer_id": subject,
        "ticker": "ANT" if subject == "ANT" else (LINK_BY_ID[subject]["ticker"] or None) if subject else None,
        "event_direction": None, "strength": None, "confidence": None, "materiality": None,
        "novelty": 1.0 if r["_cluster"] == f"ocn:{r['id']}" else 0.0,
        "horizon_days": hl * 2, "half_life_days": hl,
        "event_at": None, "published_at": pub.isoformat() if pub else None,
        "detected_at": seen.isoformat() if seen else None, "valid_until": None,
        "cluster_id": r["_cluster"], "evidence": evidence,
        "details": {"publisher": source, "entities": None,
                    "matched_issuers": (["ANT"] if r["_match"] else []) + in_title,
                    "matched_in_title": (["ANT"] if r["_ant_in_title"] else []) + in_title},
        "producer": "anthropic-ipo-hackathon/scripts/build_signals.py", "model": None, "prompt_version": None,
        "signal_type": "state" if cat in STATE_CATEGORIES else "event", "superseded_by": None, "superseded_at": None,
        # OCN's detected_at is kept as the real point-in-time; backfilled marks rows it found long after publication
        "backfilled": bool(pub and seen and seen - pub > timedelta(days=7)),
        "_ocn": {"article_id": r["id"], "run_id": r.get("run_id"), "domains": r["_domains"], "source_category": cat,
                 "match_terms": r["_match"], "raw_title": r["_raw_title"], "also_reported_by": r["_also"],
                 "av_overall_sentiment": m.get("overall_sentiment_label")},
    }


def impacts(r: dict, s: dict) -> list[dict]:
    """Key is (signal_id, issuer_id, link_type). ANT gets a direct row when the story mentions Anthropic
    (confidence 1.0 in the headline, 0.5 body only), plus one row per link type of each company the headline
    names, so a Google story reaches ANT as both supplier and competitor."""
    av = {t["ticker"]: AV_DIRECTION.get(t.get("ticker_sentiment_label")) for t in r["metadata"].get("ticker_sentiment") or []}
    av["GOOGL"] = av.get("GOOGL", av.get("GOOG"))
    row = lambda issuer, lt, hops, direction, src, conf: {
        "signal_id": s["signal_id"], "issuer_id": issuer, "link_type": lt, "link_hops": hops, "direction": direction,
        "exposure": None, "exposure_source": src, "link_confidence": conf}
    out = []
    if r["_match"]:
        out.append(row("ANT", "direct", 0, None, "not measured", 1.0 if r["_ant_in_title"] else 0.5))
    if not r["_ant_in_title"]:
        for lt in sorted({t for l in r["_links"] for t in l["types"]}):
            out.append(row("ANT", lt, 1, None, "not measured", LINK_CONFIDENCE[lt]))
    for l in r["_links"]:
        t = LINK_BY_ID[l["issuer_id"]]["ticker"]
        d = av.get(t) if t else None
        out.append(row(l["issuer_id"], "direct", 0, d, "Alpha Vantage ticker sentiment" if d is not None else "not measured", 1.0))
    return out


# ---- OCN's graded families (context/ocn/raw/signals/<source_type>.jsonl.gz) -> signal.v1.1 ----
SIGNALS_RAW = ROOT / "context/ocn/raw/signals"
DETECTOR = {"taiwan_market_signal": "taiwan", "korea_market_signal": "korea", "japan_market_signal": "japan",
            "china_market_signal": "china", "middle_east_market_signal": "middle_east", "geopolitical_signal": "geo",
            "macro_signal": "macro"}
EXCHANGE = {"taiwan": ("TWSE", "TW"), "korea": ("KRX", "KR"), "japan": ("TSE", "JP"), "china": ("CN", "CN")}
LOCAL = {c: l["issuer_id"] for l in LINKS for c in l.get("local_codes", "").split()}
TICKER_TO_ID = {l["ticker"]: l["issuer_id"] for l in LINKS if l["ticker"]} | {"GOOG": "GOOGL"}
ANT_MATCH = re.compile(r"\banthropic\b|\bamodei\b|\bclaude[ -](?:code|cowork|opus|sonnet|haiku|fable|mythos|\d)|\bclaude\.ai\b", re.I)
LEVEL = {"high": 0.9, "medium": 0.6, "low": 0.3, "none": 0.0}
NOVELTY = {"step_change": 1.0, "meaningful_update": 0.66, "incremental_update": 0.33, "repeated_coverage": 0.0}
RELATIONSHIP = {"competitor": "competitor", "supplier": "supplier", "customer": "customer"}
MACRO_PRIOR, GEO_PRIOR, REGIONAL_PRIOR = 4, 15, 4
KR_FILINGS = {"supply_contract", "capacity_commitment", "preliminary_earnings", "guidance_disclosure", "rumour_adjudication",
              "voluntary_clarification", "rumour_confirmation"}


def sign(x) -> int | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return (v > 0) - (v < 0)


def graded_kind(det: str, cat: str | None) -> str:
    if det == "macro":
        return "data_release"
    if det == "taiwan":
        return "exchange_disclosure" if (cat or "").startswith("mops") else "news_article"
    if det == "korea":
        return "exchange_disclosure" if cat in KR_FILINGS else "data_release" if cat == "export_surprise" else "news_article"
    if det == "japan":
        return "news_article" if cat == "jp_press" else "exchange_disclosure"
    if det == "china":
        return {"cn_disclosure": "exchange_disclosure", "cn_policy": "government_document", "cn_trade": "data_release"}.get(cat, "news_article")
    return "news_article"


def graded(st: str, row: dict) -> tuple[dict, list[dict]]:
    det, m = DETECTOR[st], row.get("metadata") or {}
    cat = m.get("source_category")
    rid = f"ocn:{st}:{row['id']}"
    original = " ".join((row.get("title") or "").split())
    if det == "macro":
        series = m.get("member_series") or [m.get("series_id")]
        name = m.get("series_name") or m.get("release_name") or ", ".join(s for s in series if s)
        title = f"{name}: {row.get('signal_reason') or m.get('event') or m.get('signal_type')}"[:300]
    else:
        title = display_title(m.get("translated_title") or original, cat) or display_title(original, cat)
    lang = language(original, m.get("language"))
    text = " ".join(str(x or "") for x in (original, m.get("translated_title"), row.get("signal_reason"), m.get("summary")))
    # companies that link this row to Anthropic
    named = {l["issuer_id"] for l in LINKS if l["re"].search(text)}
    code = str(m.get("ticker") or m.get("code") or m.get("stock_code") or "")
    if code in LOCAL:
        named.add(LOCAL[code])
    me_dir = {}
    for e in row.get("entities") or []:
        if isinstance(e, dict) and e.get("ticker") in TICKER_TO_ID:
            named.add(TICKER_TO_ID[e["ticker"]])
            me_dir[TICKER_TO_ID[e["ticker"]]] = {"rise": 1, "fall": -1}.get(e.get("share_price"))
    read_through = [(TICKER_TO_ID[t], rt) for rt in m.get("read_through") or [] for t in rt.get("tickers") or [] if t in TICKER_TO_ID]
    named |= {i for i, _ in read_through}
    ant = bool(ANT_MATCH.search(text))
    # subject: the regional company itself
    subject, ticker = None, None
    if det in EXCHANGE and code:
        subject, ticker = LOCAL.get(code) or f"{EXCHANGE[det][0]}:{code}", code
    scope = ("macro" if det == "macro" else "geopolitical" if det in ("geo", "middle_east")
             else "policy" if cat == "cn_policy" else "company" if subject else "sector")
    direction = None
    if det == "taiwan":
        direction = sign(m.get("revenue_deviation"))
    elif det == "japan":
        direction = sign(m.get("operating_profit_pct_change") if m.get("operating_profit_pct_change") is not None else m.get("revenue_pct_change"))
    elif det == "middle_east":
        direction = {"good": 1, "bad": -1, "mixed": 0}.get(m.get("news_effect"))
    ev = m.get("event") if isinstance(m.get("event"), dict) else {}
    event_at = ts(str(m.get("fact_occurred_date") or m.get("statement_date") or (m.get("press_event") or {}).get("date")
                      or ev.get("date") or m.get("event_date") or m.get("observation_date") or ""))
    pub, seen = ts(row.get("published") or ""), ts(row.get("stored_at") or "")
    kind = graded_kind(det, cat)
    src = m.get("source") if isinstance(m.get("source"), dict) else {}
    publisher = (m.get("outlet") or src.get("name") or (m.get("release_agency") and f"{m['release_agency']} {m.get('release_name') or ''}".strip())
                 or outlet(row.get("url") or "") or {"taiwan": "MOPS", "korea": "DART", "japan": "TDnet", "china": "CNINFO/HKEX"}.get(det, "OCN"))
    hay = f"{publisher} {outlet(row.get('url') or '')}".lower()
    reliability = (0.9 if kind in ("exchange_disclosure", "government_document", "data_release")
                   else next((p for p, rx in RELIABILITY if re.search(rx, hay)), 0.6))
    cats = sorted(set(row.get("category") or []) | set(categories({"_title": title, "summary": m.get("summary")})))
    hl = max((h for n, h, _ in CATEGORIES if n in cats), default=None) or (
        MACRO_PRIOR if det == "macro" else GEO_PRIOR if det in ("geo", "middle_east") else REGIONAL_PRIOR)
    summary = m.get("translated_summary") or m.get("summary") or (row.get("signal_reason") if det in ("macro", "geo") else None)
    region = EXCHANGE.get(det, ("", "US" if det == "macro" else "ME" if det == "middle_east" else "global"))[1]
    affected = [{"name": e.get("name"), "ticker": e.get("ticker"), "share_price": e.get("share_price"), "relationship": e.get("relationship")}
                for e in row.get("entities") or [] if isinstance(e, dict) and e.get("role") == "affected"]
    typed = {
        "taiwan": {"local_code": code or None, "source_category": cat, "revenue_deviation_pct": m.get("revenue_deviation"), "clause_label": m.get("clause_label")},
        "korea": {"stock_code": code or None, "report_type": cat},
        "japan": {"local_code": code or None, "source_category": cat, "typical_direction": m.get("habit_typical_direction")},
        "china": {"local_code": code or None, "signal_type": m.get("signal_type"), "link_direction": m.get("direction")},
        "middle_east": {"affected_companies": affected, "effect": m.get("news_effect")},
        "geo": {"channel": m.get("channel"), "assets": m.get("assets") or []},
        "macro": {"series_id": [s for s in (m.get("member_series") or [m.get("series_id")]) if s], "channel": m.get("channel"),
                  "transmission": m.get("transmission"), "z_score": m.get("z_scores") or m.get("z_score"), "move_bp": m.get("move_bp")},
    }[det]
    extracted = m.get("extracted_by") if isinstance(m.get("extracted_by"), dict) else {}
    s = {
        "signal_id": rid, "schema_version": SCHEMA_VERSION,
        "source_kind": kind, "source_name": publisher, "source_url": row.get("url") or src.get("url") or m.get("filing_url") or m.get("pdf_url")
        or (f"https://fred.stlouisfed.org/series/{typed['series_id'][0]}" if det == "macro" and typed["series_id"] else ""),
        "source_reliability": reliability,
        "scope": scope, "detector": det, "subtype": cat or m.get("signal_type") or m.get("channel") or st,
        "signal_detection": row["signal_detection"], "category": cats,
        "title": title, "title_original": original if (original and original != title) else None, "language": lang if det != "macro" else "en",
        "summary": (summary or "")[:600] or None,
        "region": region, "country": region if region in ("TW", "KR", "JP", "CN", "US") else None,
        "subject_issuer_id": subject, "ticker": ticker,
        "event_direction": direction,
        "strength": float(row["signal_score"]) if row.get("signal_score") is not None else family_strength(det, m),
        "confidence": LEVEL.get(row.get("confidence")), "materiality": LEVEL.get(row.get("materiality")),
        "novelty": NOVELTY.get(row.get("novelty")),
        "horizon_days": hl * 2, "half_life_days": hl,
        "event_at": event_at.isoformat() if event_at else None,
        "published_at": pub.isoformat() if pub else None, "detected_at": seen.isoformat() if seen else None, "valid_until": None,
        "cluster_id": rid, "evidence": row.get("signal_reason"),
        "details": {"publisher": publisher, "entities": row.get("entities") or None,
                    "matched_issuers": (["ANT"] if ant else []) + sorted(named), "matched_in_title": [], **typed},
        "producer": "ocn/signal-detection-agent", "model": extracted.get("model"), "prompt_version": extracted.get("prompt"),
        "signal_type": "state" if m.get("signal_type") == "level" else "event", "superseded_by": None, "superseded_at": None,
        "backfilled": bool(pub and seen and seen - pub > timedelta(days=7)),
        "_ocn": {"result_id": row["id"], "source_type": st, "article_id": row.get("article_id"), "source_id": row.get("source_id"),
                 "corroboration": (m.get("corroboration") or {}).get("count"), "also_reported_by": m.get("also_reported_by") or []},
    }
    row_ = lambda issuer, lt, hops, d, src_, conf: {"signal_id": rid, "issuer_id": issuer, "link_type": lt, "link_hops": hops,
                                                   "direction": d, "exposure": None, "exposure_source": src_, "link_confidence": conf}
    imps = []
    if ant:
        imps.append(row_("ANT", "direct", 0, None, "not measured", 1.0))
    else:
        for lt in sorted({t for i in named for t in LINK_BY_ID[i]["types"]}):
            imps.append(row_("ANT", lt, 1, None, "not measured", LINK_CONFIDENCE[lt]))
        if det in ("macro", "geo", "middle_east"):
            # ponytail: a flat prior until ANT has a price history to measure its rate and risk beta
            imps.append(row_("ANT", "macro", 1, None, "prior: growth IPO rate and risk sensitivity", 0.3))
    rt_dir = {i: rt.get("relationship") for i, rt in read_through}
    for i in sorted(named):
        d = me_dir.get(i) if i in me_dir else (direction if i == subject else None)
        imps.append(row_(i, "direct", 0, d, "OCN share_price effect" if i in me_dir and d is not None else "not measured", 1.0))
    if subject and subject not in named and subject not in LINK_BY_ID:
        imps.append(row_(subject, "direct", 0, direction, "not measured", 1.0))
    for i, rel in rt_dir.items():
        if rel in RELATIONSHIP and not any(x["issuer_id"] == i and x["link_type"] == RELATIONSHIP[rel] for x in imps):
            imps.append(row_(i, RELATIONSHIP[rel], 1, None, "OCN read_through", 0.6))
    return s, imps


def load_graded(raw: Path = SIGNALS_RAW) -> list[tuple[dict, list[dict]]]:
    out = []
    for st in DETECTOR:
        f = raw / f"{st}.jsonl.gz"
        if f.exists():
            out += [graded(st, json.loads(l)) for l in gzip.open(f, "rt", encoding="utf-8")]
    return out


def supersede(sigs: list[dict], periods: dict[str, str | None]) -> None:
    """State signals hold until the next one replaces them: each company's quarterly results are
    superseded by its next quarter's, at the time that next one was detected."""
    chains = defaultdict(list)
    for s in sigs:
        if s["signal_type"] == "state" and periods.get(s["signal_id"]):
            chains[(s["subject_issuer_id"], s["_ocn"]["source_category"])].append(s)
    for chain in chains.values():
        chain.sort(key=lambda s: periods[s["signal_id"]])
        for old, new in zip(chain, chain[1:]):
            old["superseded_by"], old["superseded_at"] = new["signal_id"], new["detected_at"]


def build(raw: Path = RAW, signals_raw: Path = SIGNALS_RAW) -> tuple[list[dict], list[dict], dict]:
    rows = load(raw)
    n = len(rows)
    rows = dedup_url(rows)
    n_url = len(rows)
    rows, dropped = dedup_titles(rows)
    cluster(rows)
    NEWS_GRADES.clear()
    NEWS_GRADES.update(load_news_grades(signals_raw))
    grades = load_graded(signals_raw)
    graded_articles = {str(s["_ocn"]["article_id"]) for s, _ in grades if s["_ocn"]["article_id"]}
    # an article's place in its story, so a graded family row that replaces it inherits the cluster and novelty
    story = {str(r["id"]): (r["_cluster"], 1.0 if r["_cluster"] == f"ocn:{r['id']}" else 0.0) for r in rows}
    sigs, imps, noise, replaced, ocn_graded = [], [], Counter(), 0, 0
    for r in sorted(rows, key=lambda r: r["_t"], reverse=True):
        if str(r["id"]) in graded_articles:
            replaced += 1  # OCN graded this article itself; its graded row below wins
            continue
        s = signal(r)
        apply_news_grade(s, r)
        ocn_graded += s["producer"].startswith("ocn")
        if s["signal_detection"] == "noise":
            noise["ocn: noise" if s["producer"].startswith("ocn") else s["evidence"]] += 1  # only what can move the IPO stays
            continue
        sigs.append(s)
        imps += impacts(r, s)
    supersede(sigs, {f"ocn:{r['id']}": r["metadata"].get("period") for r in rows})
    for s, i in grades:
        a = str(s["_ocn"]["article_id"])
        if a in story:
            s["cluster_id"] = story[a][0]
        if s["novelty"] is None:
            s["novelty"] = story[a][1] if a in story else 1.0  # one row per OCN event, so it is the first report
        sigs.append(s)
        imps += i
    kept = {s["signal_id"] for s in sigs}
    imps = [i for i in imps if i["signal_id"] in kept]
    sigs.sort(key=lambda s: s["published_at"] or s["detected_at"] or "", reverse=True)
    ant = [i for i in imps if i["issuer_id"] == "ANT"]
    stats = {"articles_in": n, "after_url_dedup": n_url, "title_duplicates_merged": dropped,
             "articles_replaced_by_ocn_grade": replaced, "news_graded_by_ocn": ocn_graded, "noise_left_out": dict(noise.most_common()),
             "signals": len(sigs), "clusters": len({s["cluster_id"] for s in sigs}), "impacts": len(imps),
             "by_detector": dict(Counter(s["detector"] for s in sigs).most_common()),
             "signal_detection": dict(Counter(s["signal_detection"] for s in sigs)),
             "category": dict(Counter(c for s in sigs for c in s["category"]).most_common()),
             "uncategorised": sum(1 for s in sigs if not s["category"]),
             "about_anthropic": sum(1 for s in sigs if s["subject_issuer_id"] == "ANT"),
             "ant_impacts_by_link": dict(sorted(Counter(i["link_type"] for i in ant).items())),
             "source_kind": dict(Counter(s["source_kind"] for s in sigs)),
             "backfilled": sum(1 for s in sigs if s["backfilled"])}
    return sigs, imps, stats


def selftest() -> None:
    assert canonical_url("https://www.FT.com/content/x/?utm_source=a&syn-25=1&id=3#frag") == "https://ft.com/content/x?id=3"
    t0 = datetime(2026, 11, 10, tzinfo=timezone.utc)

    def mk(i, title, h, dom="ai_news", emb=None, match=("anthropic",), meta=None, **kw):
        r = {"id": i, "title": title, "domain": dom, "url": f"https://o{i}.com/a", "metadata": {**(meta or {}), "title_embedding": emb},
             "_match": list(match), "published": (t0 + timedelta(hours=h)).isoformat(), "created_at": (t0 + timedelta(hours=h + 1)).isoformat(),
             "source": "Reuters", "run_id": "1", **kw}
        return prepare(r)

    rows = [mk(1, "Anthropic files for IPO", 0), mk(2, "Anthropic files for IPO!", 5), mk(3, "Anthropic files for IPO", 60),
            mk(4, "Anthropic totally different", 1, emb=[1, 0]), mk(5, "Anthropic other words entirely", 2, emb=[0.99, 0.05]),
            mk(6, "Anthropic files for IPO", 1, dom="company_news")]
    kept, dropped = dedup_titles(rows)
    ids = sorted(r["id"] for r in kept)
    assert ids == [1, 3, 4, 6], ids               # 2: verbatim within 48h; 5: embedding cosine > 0.90; 3: outside window; 6: domain not title-deduped
    assert dropped == 2 and next(r for r in kept if r["id"] == 1)["_also"][0]["outlet"] == "o2.com"
    cluster(kept)
    assert next(r for r in kept if r["id"] == 6)["_cluster"] == "ocn:1"   # same story, other domain, same cluster
    assert next(r for r in kept if r["id"] == 3)["_cluster"] == "ocn:3"

    def sig(r):
        r["_cluster"] = f"ocn:{r['id']}"
        s = signal(r)
        return s, {(i["issuer_id"], i["link_type"]): i for i in impacts(r, s)}

    # linked companies only: ANT reached through each link type; Google is supplier and competitor
    s, imp = sig(mk(7, "OpenAI buys Google TPUs", 0, match=(), meta={"ticker_sentiment": [{"ticker": "GOOG", "ticker_sentiment_label": "Bullish"}]}))
    assert s["scope"] == "sector" and s["subject_issuer_id"] is None and s["signal_detection"] == "signal"
    assert {k for k in imp if k[0] == "ANT"} == {("ANT", "competitor"), ("ANT", "supplier")}
    assert imp[("GOOGL", "direct")]["direction"] == 1 and imp[("OPENAI", "direct")]["direction"] is None
    # Anthropic only in the body of a Microsoft headline: subject MSFT, ANT direct at 0.5 plus its links
    s, imp = sig(mk(8, "Microsoft president says UK should lead AI rules", 0))
    assert s["subject_issuer_id"] == "MSFT" and imp[("ANT", "direct")]["link_confidence"] == 0.5 and ("ANT", "supplier") in imp
    assert "regulation" in s["category"]
    # Anthropic only in the body, no linked company: weak
    s, _ = sig(mk(9, "Train models for free with Unsloth", 0))
    assert s["signal_detection"] == "weak_signal" and s["subject_issuer_id"] == "ANT"
    # noise templates, but never when Anthropic is in the headline
    assert sig(mk(10, "Mn Services Purchases 4,300 Shares of Salesforce Inc. $CRM", 0, match=()))[0]["signal_detection"] == "noise"
    assert sig(mk(11, "Form 4 Palantir Technologies Inc For: 8 November", 0, match=()))[0]["evidence"] == "rule: insider_filing"
    assert sig(mk(12, "Google raises stake in Anthropic", 0))[0]["signal_detection"] == "signal"
    assert sig(mk(16, "Datadog's stock jumps 31% on crushing earnings beat", 0))[0]["signal_detection"] != "noise"
    assert sig(mk(17, "Arista Networks (NYSE:ANET) Stock Falls 1.1% - What's Next?", 0, match=()))[0]["signal_detection"] == "noise"
    # quarterly results are state signals; each is superseded by the next quarter's
    q = [signal({**mk(20 + i, f"SK Hynix (000660): 2026-Q{i} results", i, dom="korea_market_signal", match=(),
                      meta={"source_category": "kr_quarterly_results", "period": f"2026-Q{i}"}), "_cluster": "x"}) for i in (2, 1)]
    supersede(q, {q[0]["signal_id"]: "2026-Q2", q[1]["signal_id"]: "2026-Q1"})
    assert q[1]["superseded_by"] == q[0]["signal_id"] and q[0]["superseded_by"] is None and q[0]["signal_type"] == "state"

    # OCN graded families
    base = {"id": 1, "signal_detection": "signal", "signal_score": None, "published": "2026-11-10T00:00:00Z",
            "stored_at": "2026-11-11T14:00:00Z", "url": None, "entities": []}
    s, imp = graded("taiwan_market_signal", {**base, "title": "台積電 (2330): 代子公司TSMC Partners公告擬投資OpenAI Group PBC",
                                             "metadata": {"ticker": "2330", "source_category": "mops_material", "fact_occurred_date": "2026-11-10",
                                                          "translated_title": "Subsidiary TSMC Partners to invest in OpenAI Group PBC"}})
    links_ = {(i["issuer_id"], i["link_type"]) for i in imp}
    assert s["detector"] == "taiwan" and s["subject_issuer_id"] == "TSM" and s["source_kind"] == "exchange_disclosure"
    assert s["title"].startswith("Subsidiary TSMC") and s["title_original"].startswith("台積電") and s["language"] == "zh"
    assert {("ANT", "supplier"), ("ANT", "competitor"), ("TSM", "direct"), ("OPENAI", "direct")} <= links_ and s["event_at"].startswith("2026-11-10")
    s, imp = graded("middle_east_market_signal", {**base, "title": "Houthis claim attack on Yanbu", "metadata": {"news_effect": "bad", "event": {"date": "2026-11-12"}},
                                                  "entities": [{"name": "Nvidia", "ticker": "NVDA", "role": "affected", "share_price": "fall"}]})
    d = {(i["issuer_id"], i["link_type"]): i for i in imp}
    assert s["scope"] == "geopolitical" and s["event_direction"] == -1 and d[("NVDA", "direct")]["direction"] == -1
    assert ("ANT", "supplier") in d and ("ANT", "macro") in d and s["details"]["effect"] == "bad"
    s, imp = graded("macro_signal", {**base, "title": None, "signal_reason": "Entered the top 1% of its 20-year range at 2.78",
                                     "metadata": {"signal_type": "level", "series_id": "DFII10", "series_name": "10-Year TIPS Yield", "release_agency": "FOMC",
                                                  "release_name": "H.15", "observation_date": "2026-11-10"}})
    assert s["detector"] == "macro" and s["scope"] == "macro" and s["source_kind"] == "data_release" and s["signal_type"] == "state"
    assert s["title"].startswith("10-Year TIPS Yield: ") and [i["link_type"] for i in imp] == ["macro"] and s["details"]["series_id"] == ["DFII10"]
    s, imp = graded("china_market_signal", {**base, "title": "SMEE wins lithography order", "metadata": {"code": "688012", "source_category": "cn_press",
                                            "read_through": [{"tickers": ["AMD"], "direction": "opposite", "relationship": "competitor"}]}})
    assert ("AMD", "competitor") in {(i["issuer_id"], i["link_type"]) for i in imp} and s["subject_issuer_id"] == "CN:688012"
    # strength from each family's own size measure
    assert family_strength("macro", {"z_scores": {"DGS2": 2.0, "DGS10": -3.0}}) == 0.75
    assert family_strength("macro", {"percentile": 99.12}) == 0.982 and family_strength("china", {"deviation_mads": 19.6}) == 1.0
    assert family_strength("geo", {}) is None
    # OCN's AI-news grade replaces the rules; a noise grade keeps Anthropic headlines as weak
    r = mk(30, "AWS launches Chile cloud region", 0, match=())
    s = signal({**r, "_cluster": "ocn:30"})
    NEWS_GRADES.clear()
    NEWS_GRADES["30"] = {"id": 9, "signal_detection": "signal", "signal_score": "0.76", "confidence": "high", "materiality": "medium",
                         "novelty": "step_change", "category": ["Cloud & Compute Platforms"], "signal_reason": "AWS opened a region"}
    apply_news_grade(s, r)
    assert (s["strength"], s["confidence"], s["materiality"], s["novelty"]) == (0.76, 0.9, 0.6, 1.0) and s["producer"] == "ocn/signal-detection-agent"
    r = mk(31, "Anthropic CEO interview", 0)
    s = signal({**r, "_cluster": "ocn:31"})
    NEWS_GRADES["31"] = {"signal_detection": "noise", "signal_score": "0.2", "confidence": None, "materiality": "none", "novelty": None}
    apply_news_grade(s, r)
    assert s["signal_detection"] == "weak_signal" and s["materiality"] == 0.0
    s = signal({**mk(32, "Nvidia unveils chip", 0, match=()), "_cluster": "ocn:32"})
    apply_news_grade(s, mk(32, "Nvidia unveils chip", 0, match=()))
    assert s["model"] == "rules" and s["prompt_version"] == RULES_VERSION
    NEWS_GRADES.clear()
    # regional rows: prefix dropped from the title, Hana Micron is not Micron, DART filings are disclosures
    assert mk(13, "Hana Micron (067310) [20261112900988]: 타인에대한채무보증결정", 0, match=()) is None
    s, _ = sig(mk(14, "SK Hynix (000660) [20251215801345]: 주주명부폐쇄기간또는기준일설정", 0, dom="korea_market_signal", match=(),
                  meta={"source_category": "dart_filing"}))
    assert s["source_kind"] == "exchange_disclosure" and s["title"].startswith("SK Hynix: ") and s["language"] == "ko"
    assert s["title_original"] == s["title"] and s["country"] == "KR" and s["source_reliability"] == 0.9
    s, _ = sig(mk(15, "Samsung Electronics (005930) [English coverage check]: Samsung wins Nvidia HBM order", 0, dom="korea_market_signal", match=()))
    assert s["title"] == "Samsung wins Nvidia HBM order" and "supply" in s["category"]
    print("selftest ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--raw", type=Path, default=RAW)
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    sigs, imps, stats = build(a.raw)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in (("signals.v1.jsonl", sigs), ("signal_impacts.v1.jsonl", imps)):
        with open(OUT / name, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)
    (OUT / "stats.json").write_text(json.dumps({"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **stats}, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
