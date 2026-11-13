#!/usr/bin/env python3
"""Turn the raw OCN pull (context/ocn/raw/articles/) into signal.v1 rows. Standard library only.

  python scripts/build_signals.py            # writes context/ocn/signals/
  python scripts/build_signals.py --selftest

Dedup follows OCN's news-retrieval, in the same order:
  1. URL: one row per canonical URL; the same story filed under two OCN domains becomes one row.
  2. Headline near-duplicates, per OCN domain, inside OCN's time window and similarity threshold for
     that domain. The dropped copy is recorded on the kept row in also_reported_by as {outlet, title, url}.
  3. cluster_id: rows from different domains about the same story share a cluster, so the event
     counts once. The first to report it gets novelty 1, later ones 0.
Similarity is the cosine of OCN's own title embeddings (text-embedding-3-small) when both rows carry one.
"""
import argparse, csv, difflib, gzip, json, math, re, sys, urllib.parse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "context/ocn/raw/articles", ROOT / "context/ocn/signals"

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
# Starting priors from signal.v1 (Overall-plan.md); calibration replaces them.
RELIABILITY = [(0.8, r"reuters|bloomberg|associated press|apnews|dow jones|marketwatch"),
               (0.7, r"financial times|ft\.com|wall street journal|wsj|new york times|nytimes|cnbc|guardian|techcrunch|the verge|"
                     r"mit technology review|the information|axios|fortune|business insider|forbes|wired|ars technica|semafor|economist")]
LINK_CONFIDENCE = {"supplier": 0.8, "competitor": 0.6, "customer": 0.5, "peer": 0.4, "sector": 0.4}
LINK_PRIORITY = ["competitor", "supplier", "customer", "peer", "sector"]
AV_DIRECTION = {"Bullish": 1, "Somewhat-Bullish": 1, "Neutral": 0, "Somewhat-Bearish": -1, "Bearish": -1}
LINKS = {l["issuer_id"]: l for l in csv.DictReader(open(ROOT / "context/markets/links.csv"))}


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


def similar(a: dict, b: dict) -> tuple[float, float]:
    """(similarity, threshold scale): cosine of stored embeddings, else text ratio judged against TEXT_THRESHOLD."""
    if a["_emb"] and b["_emb"]:
        return sum(x * y for x, y in zip(a["_emb"], b["_emb"])), 1.0
    sm = difflib.SequenceMatcher(None, a["_norm"], b["_norm"])
    if sm.real_quick_ratio() < TEXT_THRESHOLD or sm.quick_ratio() < TEXT_THRESHOLD:
        return 0.0, 0.0
    return sm.ratio(), 0.0


def is_dup(a: dict, b: dict, threshold: float) -> bool:
    s, emb = similar(a, b)
    return s >= (threshold if emb else TEXT_THRESHOLD)


def load(raw: Path) -> list[dict]:
    rows = []
    for f in sorted(raw.glob("*.jsonl.gz")):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            r = json.loads(line)
            m = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            r["metadata"] = m
            r["_url"] = m.get("article_url") if str(r.get("url", "")).startswith("kr-english-check://") else r.get("url")
            r["_pub"], r["_seen"] = ts(r.get("published") or ""), ts(r.get("created_at") or "")
            r["_t"] = r["_pub"] or r["_seen"]
            r["_emb"], r["_norm"] = unit(m.get("title_embedding")), norm_title(r.get("title"))
            r["_domains"], r["_also"] = [r["domain"]], list(m.get("also_reported_by") or [])
            r.setdefault("_match", [])
            # Alpha Vantage tags every ticker an article mentions, so a tag alone is not "about"; only headline links count
            r["_links"] = [l for l in r.get("_links", []) if l.get("via") != "ticker_sentiment"]
            if r["_match"] or r["_links"]:
                rows.append(r)
    return [r for r in rows if r["_t"] and r.get("title")]


def dedup_url(rows: list[dict]) -> list[dict]:
    kept: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["_seen"] or r["_t"]):
        k = canonical_url(r["_url"] or "")
        if k not in kept:
            kept[k] = r
            continue
        a = kept[k]
        a["_domains"] = sorted(set(a["_domains"]) | {r["domain"]})
        a["_match"] = sorted(set(a["_match"]) | set(r["_match"]))
        a["_links"] = sorted({l["issuer_id"]: l for l in a["_links"] + r["_links"]}.values(), key=lambda l: l["issuer_id"])
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
            entry = {"outlet": outlet(r["_url"] or ""), "title": " ".join(r["title"].split())[:300], "url": r["_url"]}
            if entry["outlet"] and entry["url"] not in {e.get("url") for e in match["_also"] if isinstance(e, dict)}:
                match["_also"].append(entry)
            match["_match"] = sorted(set(match["_match"]) | set(r["_match"]))
            match["_links"] = sorted({l["issuer_id"]: l for l in match["_links"] + r["_links"]}.values(), key=lambda l: l["issuer_id"])
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


def signal(r: dict) -> dict:
    url, source = r["_url"] or "", r.get("source") or outlet(r["_url"] or "")
    long_form = "substack.com" in url or re.search(r"newsletter|substack", source, re.I)
    hay = f"{source} {outlet(url)}".lower()
    reliability = 0.5 if long_form else next((p for p, rx in RELIABILITY if re.search(rx, hay)), 0.6)
    linked = [l["issuer_id"] for l in r["_links"]]
    ant = bool(r["_match"])
    # the headline decides the subject: Anthropic named only in the body of a Microsoft story makes it a Microsoft story
    in_title = [l["issuer_id"] for l in r["_links"] if l.get("via") == "title"]
    ant_in_title = any(t in r["title"].lower() for t in r["_match"])
    named = in_title or linked
    subject = "ANT" if ant and (ant_in_title or not in_title) else (named[0] if len(named) == 1 else None)
    domain = r["domain"]
    half_life = 25 if long_form else 15 if domain in ("geopolitical_news", "middle_east_market_signal") else 4
    summary = r.get("summary") if r.get("summary") not in (None, "None", "") else None
    return {
        "signal_id": f"ocn:{r['id']}", "schema_version": 1,
        "source_kind": "long_form" if long_form else "news_article", "source_name": source, "source_url": url,
        "author": None, "author_id": None, "source_reliability": reliability, "engagement": None,
        "parent_id": None, "thread_position": None,
        "scope": "company" if subject else "sector",
        "detector": "news", "subtype": domain,
        "title": " ".join(r["title"].split()), "title_original": None, "language": r["metadata"].get("language") or "en",
        "summary": summary[:600] if summary else None,
        "region": REGION.get(domain, "global"), "country": None,
        "subject_issuer_id": subject, "ticker": "ANT" if subject == "ANT" else (LINKS[subject]["ticker"] or None) if subject else None,
        "event_direction": None, "strength": None, "confidence": None, "materiality": None,
        "novelty": 1.0 if r["_cluster"] == f"ocn:{r['id']}" else 0.0,
        "horizon_days": half_life + 1 if half_life == 4 else half_life * 2, "half_life_days": half_life,
        "event_at": None, "published_at": r["_pub"].isoformat() if r["_pub"] else None,
        "detected_at": r["_seen"].isoformat() if r["_seen"] else None, "valid_until": None,
        "cluster_id": r["_cluster"], "evidence": None,
        "details": {"publisher": source, "entities": None, "matched_issuers": (["ANT"] if ant else []) + linked},
        "producer": "anthropic-ipo-hackathon/scripts/build_signals.py", "model": None, "prompt_version": None,
        "signal_type": "event", "superseded_by": None, "superseded_at": None, "backfilled": False,
        "_ocn": {"article_id": r["id"], "run_id": r.get("run_id"), "domains": r["_domains"], "match_terms": r["_match"],
                 "also_reported_by": r["_also"],
                 "av_overall_sentiment": r["metadata"].get("overall_sentiment_label")},
    }


def impacts(r: dict, s: dict) -> list[dict]:
    """ANT always gets a row (direct, or through its strongest link); each named linked company gets a direct row."""
    av = {t["ticker"]: AV_DIRECTION.get(t.get("ticker_sentiment_label")) for t in r["metadata"].get("ticker_sentiment") or []}
    av["GOOGL"] = av.get("GOOGL", av.get("GOOG"))
    out = []
    if r["_match"]:
        out.append({"signal_id": s["signal_id"], "issuer_id": "ANT", "link_type": "direct", "link_hops": 0, "direction": None,
                    "exposure": None, "exposure_source": "not measured", "link_confidence": 1.0})
    else:
        lt = min((l["link_type"] for l in r["_links"]), key=LINK_PRIORITY.index)
        out.append({"signal_id": s["signal_id"], "issuer_id": "ANT", "link_type": lt, "link_hops": 1, "direction": None,
                    "exposure": None, "exposure_source": "not measured", "link_confidence": LINK_CONFIDENCE[lt]})
    for l in r["_links"]:
        t = LINKS[l["issuer_id"]]["ticker"]
        out.append({"signal_id": s["signal_id"], "issuer_id": l["issuer_id"], "link_type": "direct", "link_hops": 0,
                    "direction": av.get(t) if t else None, "exposure": None,
                    "exposure_source": "Alpha Vantage ticker sentiment" if t and av.get(t) is not None else "not measured",
                    "link_confidence": 1.0})
    return out


def build(raw: Path = RAW) -> tuple[list[dict], list[dict], dict]:
    rows = load(raw)
    n = len(rows)
    rows = dedup_url(rows)
    n_url = len(rows)
    rows, dropped = dedup_titles(rows)
    cluster(rows)
    sigs, imps = [], []
    for r in sorted(rows, key=lambda r: r["_t"], reverse=True):
        s = signal(r)
        sigs.append(s)
        imps += impacts(r, s)
    stats = {"articles_in": n, "after_url_dedup": n_url, "title_duplicates_merged": dropped, "signals": len(sigs),
             "clusters": len({s["cluster_id"] for s in sigs}), "impacts": len(imps),
             "about_anthropic": sum(1 for s in sigs if s["subject_issuer_id"] == "ANT"),
             "ant_impacts_by_link": dict(sorted(__import__("collections").Counter(
                 i["link_type"] for i in imps if i["issuer_id"] == "ANT").items()))}
    return sigs, imps, stats


def selftest() -> None:
    assert canonical_url("https://www.FT.com/content/x/?utm_source=a&syn-25=1&id=3#frag") == "https://ft.com/content/x?id=3"
    t0 = datetime(2026, 11, 10, tzinfo=timezone.utc)
    mk = lambda i, title, h, dom="ai_news", emb=None: {"id": i, "title": title, "domain": dom, "_t": t0 + timedelta(hours=h), "_url": f"https://o{i}.com/a",
                                                     "_emb": unit(emb), "_norm": norm_title(title), "_also": [], "_match": ["anthropic"], "_links": [],
                                                     "metadata": {}, "_domains": [dom]}
    rows = [mk(1, "Anthropic files for IPO", 0), mk(2, "Anthropic files for IPO!", 5), mk(3, "Anthropic files for IPO", 60),
            mk(4, "Totally different", 1, emb=[1, 0]), mk(5, "Other words entirely", 2, emb=[0.99, 0.05]),
            mk(6, "Anthropic files for IPO", 1, dom="company_news")]
    kept, dropped = dedup_titles(rows)
    ids = sorted(r["id"] for r in kept)
    assert ids == [1, 3, 4, 6], ids               # 2: verbatim within 48h; 5: embedding cosine > 0.90; 3: outside window; 6: domain not title-deduped
    assert dropped == 2 and next(r for r in kept if r["id"] == 1)["_also"][0]["outlet"] == "o2.com"
    cluster(kept)
    assert next(r for r in kept if r["id"] == 6)["_cluster"] == "ocn:1"   # same story, other domain, same cluster
    assert next(r for r in kept if r["id"] == 3)["_cluster"] == "ocn:3"
    # a story about linked companies that never names Anthropic still reaches ANT through its strongest link
    r = {**mk(7, "OpenAI buys Nvidia chips", 0), "_match": [], "_cluster": "ocn:7", "_seen": t0, "_pub": t0, "source": "Reuters",
         "_links": [{"issuer_id": "NVDA", "link_type": "supplier"}, {"issuer_id": "OPENAI", "link_type": "competitor"}],
         "metadata": {"ticker_sentiment": [{"ticker": "NVDA", "ticker_sentiment_label": "Bullish"}]}, "run_id": "1"}
    s = signal(r)
    assert s["scope"] == "sector" and s["subject_issuer_id"] is None and s["source_reliability"] == 0.8
    imp = {i["issuer_id"]: i for i in impacts(r, s)}
    assert imp["ANT"]["link_type"] == "competitor" and imp["ANT"]["link_hops"] == 1 and imp["ANT"]["link_confidence"] == 0.6
    assert imp["NVDA"]["direction"] == 1 and imp["OPENAI"]["direction"] is None
    # Anthropic only in the body of a Microsoft headline: subject MSFT, ANT still gets a direct impact
    r2 = {**r, "id": 8, "title": "Microsoft president says UK should lead AI rules", "_match": ["anthropic"],
          "_links": [{"issuer_id": "MSFT", "link_type": "supplier", "via": "title"}]}
    s2 = signal(r2)
    assert s2["subject_issuer_id"] == "MSFT" and {i["issuer_id"]: i["link_type"] for i in impacts(r2, s2)}["ANT"] == "direct"
    assert signal({**r2, "title": "Anthropic and Microsoft sign deal"})["subject_issuer_id"] == "ANT"
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
    (OUT / "stats.json").write_text(json.dumps({"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **stats}, indent=2) + "\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
