#!/usr/bin/env python3
"""Pull Anthropic-related data from OCN into context/ocn/raw/, as-is. Standard library only.

  python scripts/pull_ocn.py                                   # every OCN article that mentions Anthropic
  OCN_API_KEY=... python scripts/pull_ocn.py                   # + OCN's graded regional, geopolitical and macro signals, market data
  SEC_USER_AGENT="Name email" python scripts/pull_ocn.py       # + Anthropic's own company data from SEC EDGAR
  python scripts/pull_ocn.py --since 2026-10-01                # only articles published on or after a date

OCN does not track Anthropic as a company (no ticker until 19 Nov), so its articles are found by text.
Each OCN domain is walked with bodies and a row is kept when either:
  - it mentions Anthropic anywhere (MATCH); the matched words go in `_match`, or
  - its headline names a company linked to Anthropic in context/markets/links.csv (competitor, supplier,
    customer, peer, sector); these go in `_links`.
Rows are otherwise stored exactly as OCN returns them. scripts/build_signals.py dedups and structures them.

A finished file is skipped on the next run; delete it to pull it again.
"""
import argparse, base64, csv, gzip, json, os, re, shutil, sys, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "context/ocn/raw"
BASE = os.environ.get("OCN_BASE_URL", "http://staging-ocn-alb-1788409195.eu-north-1.elb.amazonaws.com").rstrip("/")
KEY = os.environ.get("OCN_API_KEY")
SEC_UA = os.environ.get("SEC_USER_AGENT")
CIK = "0002130357"  # Anthropic PBC
# ponytail: plain keyword match; "Claude" alone is left out because it is also a first name
MATCH = re.compile(r"\banthropic\b|\bamodei\b|\bclaude[ -](?:code|cowork|opus|sonnet|haiku|fable|mythos|\d)|\bclaude\.ai\b|\bANT\b(?= (?:ipo|shares|stock|listing))", re.I)


def get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 4:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 4:
                raise
        time.sleep(2 ** attempt)


def ocn(path: str, **params):
    query = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None])
    return get(f"{BASE}{path}{'?' + query if query else ''}", {"Authorization": f"Bearer {KEY}"} if KEY else None)


# Companies linked to Anthropic (competitor, supplier, customer, peer, sector). Matched on the headline only,
# so a passing mention in a long body does not pull in general tech news.
LINKS = list(csv.DictReader(open(ROOT / "context/markets/links.csv")))
for l in LINKS:
    l["re"] = re.compile(l["title_pattern"])


def links(row: dict) -> list[dict]:
    # Headline only. Alpha Vantage's ticker tags (company_news) mark mentions, not subjects, so they are not used here.
    return [{"issuer_id": l["issuer_id"], "link_type": l["link_type"], "via": "title"}
            for l in sorted(LINKS, key=lambda l: l["issuer_id"]) if l["re"].search(row.get("title") or "")]


def matches(row: dict) -> dict | None:
    text = " ".join(str(row.get(k) or "") for k in ("title", "summary", "body", "text", "headline")) + " " + json.dumps(row.get("metadata") or {}, ensure_ascii=False)
    terms, linked = sorted({m.lower() for m in MATCH.findall(text)}), links(row)
    return {"_match": terms, "_links": linked} if terms or linked else None


def save_json(name: str, data) -> None:
    (OUT / name).parent.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


CHUNK, WORKERS = 10_000, 8  # ponytail: 8 parallel walks keeps the shared staging API comfortable; raise if it stays fast


def cursor_at(article_id: int) -> str:
    return base64.b64encode(json.dumps({"id": article_id}).encode()).decode()  # OCN's cursor is base64 {"id": n}


def scan_chunk(domain: str, lo: int, hi: int, since: str | None) -> tuple[int, int]:
    """Walk one domain over article ids [lo, hi), newest first. Saved per chunk so a rerun resumes."""
    final = OUT / f"articles/.chunks/{domain}/{lo:07d}.jsonl.gz"
    if final.exists():
        return tuple(json.loads(final.with_suffix(".count").read_text()))
    final.parent.mkdir(parents=True, exist_ok=True)
    part, seen, kept, cursor = final.with_name(final.name + ".part"), 0, 0, cursor_at(hi)
    with gzip.open(part, "wt", encoding="utf-8") as f:
        while cursor:
            page = ocn("/news/articles", domain=domain, limit=100, include_body="true", from_date=since, cursor=cursor)
            rows = [r for r in page["articles"] if int(r["id"]) >= lo]
            for row in rows:
                if hit := matches(row):
                    f.write(json.dumps({**row, **hit}, ensure_ascii=False) + "\n")
                    kept += 1
            seen += len(rows)
            cursor = page.get("next_cursor") if len(rows) == len(page["articles"]) else None
    final.with_suffix(".count").write_text(json.dumps([kept, seen]))
    part.rename(final)
    return kept, seen


def articles(since: str | None, counts: dict) -> None:
    domains = [d["slug"] for d in ocn("/news/domains")]
    save_json("domains.json", ocn("/news/domains"))
    top = max((int(p["articles"][0]["id"]) for d in domains if (p := ocn("/news/articles", domain=d, limit=1))["articles"]), default=0) + 1
    jobs = [(d, lo, min(lo + CHUNK, top)) for d in domains if not (OUT / f"articles/{d}.jsonl.gz").exists() for lo in range(0, top, CHUNK)]
    totals: dict[str, list[int]] = {d: [0, 0] for d in domains}
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(scan_chunk, d, lo, hi, since): d for d, lo, hi in jobs}
        for fut in as_completed(futures):
            k, s = fut.result()
            totals[futures[fut]][0] += k
            totals[futures[fut]][1] += s
            done += 1
            print(f"{done}/{len(jobs)} chunks; kept {sum(t[0] for t in totals.values())} of {sum(t[1] for t in totals.values())}", flush=True)
    for d in {d for d, _, _ in jobs}:  # stitch chunks into one file per domain, newest first
        chunks = sorted((OUT / f"articles/.chunks/{d}").glob("*.jsonl.gz"), reverse=True)
        with gzip.open(OUT / f"articles/{d}.jsonl.gz", "wt", encoding="utf-8") as f:
            for c in chunks:
                f.writelines(sorted(gzip.open(c, "rt", encoding="utf-8"), key=lambda l: -int(json.loads(l)["id"])))
        counts[f"articles/{d}"] = {"kept": totals[d][0], "scanned": totals[d][1]}
        print(f"  {d}: {totals[d][0]} kept of {totals[d][1]}")
    shutil.rmtree(OUT / "articles/.chunks", ignore_errors=True)


# OCN's graded signal families, one event per row (view=events drops rows folded into a story).
# Only rows that can move the Anthropic IPO are kept (see relevant()); build_signals.py maps them into signal.v1.1.
FAMILIES = ["taiwan_market_signal", "korea_market_signal", "japan_market_signal", "china_market_signal",
            "middle_east_market_signal", "geopolitical_signal", "macro_signal"]
LOCAL_CODES = {c for l in LINKS for c in l["local_codes"].split()}
TICKERS = {l["ticker"] for l in LINKS if l["ticker"]} | {"GOOG"}
# Geopolitics reaches ANT through AI supply, demand and capital: OCN's AI-universe categories, or an AI/chip headline
# Energy & Grid and Data Centers are left out on their own: they are mostly fuel and local permitting stories
REL_W = json.load(open(ROOT / "context/ocn/weights.json"))["relevance"]
GEO_CATEGORIES = set(REL_W["geo_categories"])
GEO_ASSETS = set(REL_W["geo_assets"])
# "data center" alone is left out: local zoning and permitting stories do not move the IPO
AI_HEADLINE = re.compile(r"\b(?:ai|a\.i\.|artificial intelligence|chips?|chipmakers?|semiconductors?|gpus?|export controls?|"
                         r"entity list|hyperscalers?|llms?|hbm)\b", re.I)
# Macro reaches a growth IPO through the discount rate and risk appetite, not through energy, housing or consumer data
MACRO_CHANNELS = set(REL_W["macro_channels"])
MACRO_SERIES = re.compile(r"^(?:" + "|".join(map(re.escape, REL_W["macro_level_series_prefixes"])) + ")")


def text_of(row: dict) -> str:
    m = row.get("metadata") or {}
    return " ".join(str(x or "") for x in (row.get("title"), m.get("translated_title"), row.get("signal_reason"), m.get("summary")))


def names_linked(row: dict) -> bool:
    m, text = row.get("metadata") or {}, text_of(row)
    if MATCH.search(text) or any(l["re"].search(text) for l in LINKS):
        return True
    if str(m.get("ticker") or m.get("code") or m.get("stock_code") or "") in LOCAL_CODES:
        return True
    tickers = {e.get("ticker") for e in row.get("entities") or [] if isinstance(e, dict)}
    tickers |= {t for rt in m.get("read_through") or [] for t in rt.get("tickers") or []}
    return bool(tickers & TICKERS)


def headline_names_linked(row: dict) -> bool:
    m = row.get("metadata") or {}
    head = f"{row.get('title') or ''} {m.get('translated_title') or ''}"
    return bool(MATCH.search(head)) or any(l["re"].search(head) for l in LINKS)


def relevant(st: str, row: dict) -> bool:
    """Can this graded OCN row move the Anthropic IPO, directly or through a linked company, AI geopolitics or rates?
    A weak_signal must name Anthropic or a linked company in its headline (geo: or a chip, export-control or AI headline);
    a full signal may also qualify through OCN's tickers, read-through, categories and assets."""
    m = row.get("metadata") or {}
    label = row.get("signal_detection")
    if label not in ("signal", "weak_signal") or m.get("suppressed_by"):
        return False  # OCN judged it noise, or it is a macro audit row
    if st == "macro_signal":
        if m.get("signal_type") == "change":
            return m.get("channel") in MACRO_CHANNELS
        return bool(MACRO_SERIES.match(m.get("series_id") or ""))
    if headline_names_linked(row):
        return True
    if st == "geopolitical_signal":
        head = f"{row.get('title') or ''} {m.get('translated_title') or ''}"
        if AI_HEADLINE.search(head):
            return True
        return label == "signal" and (bool(set(row.get("category") or []) & GEO_CATEGORIES) or bool(set(m.get("assets") or []) & GEO_ASSETS))
    return label == "signal" and names_linked(row)


def article_ids() -> set[str]:
    """OCN article ids already in the article pull; OCN's AI-news grades are kept only for these."""
    return {str(json.loads(l)["id"]) for f in (OUT / "articles").glob("*.jsonl.gz") for l in gzip.open(f, "rt", encoding="utf-8")}


def ocn_results(counts: dict) -> None:
    ours = article_ids()
    keep = {"news": lambda r: str(r.get("article_id")) in ours}  # OCN's own grades (score, confidence, materiality, novelty)
    for st in ["news"] + FAMILIES:
        final = OUT / f"signals/{st}.jsonl.gz"
        final.parent.mkdir(parents=True, exist_ok=True)
        part, seen, kept, cursor = final.with_name(final.name + ".part"), 0, 0, None
        with gzip.open(part, "wt", encoding="utf-8") as f:
            while True:
                page = ocn("/agent/results", source_type=st, view="events", limit=500, cursor=cursor)
                for r in page["results"]:
                    if keep[st](r) if st in keep else relevant(st, r):
                        (r.get("metadata") or {}).pop("body", None)  # geo rows carry the full article; not needed
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                        kept += 1
                seen += len(page["results"])
                cursor = page.get("next_cursor")
                print(f"\r  signals/{st}: {kept} kept of {seen}", end="", flush=True)
                if not cursor or not page["results"]:
                    break
        part.replace(final)  # graded labels change as OCN regroups stories, so this is refreshed every run
        print()
        counts[f"signals/{st}"] = {"kept": kept, "scanned": seen}


def ocn_keyed(counts: dict) -> None:
    ocn_results(counts)
    tickers = ["ANT"] + sorted({t for row in csv.DictReader(open(ROOT / "context/markets/tickers.csv")) for t in row["tickers"].split()})
    out = {}
    for t in tickers:
        out[t] = {}
        for kind, path, params in (("quote", "quote", {}), ("overview", "overview", {}), ("price_history", "price-history", {"days": 260}),
                                   ("earnings", "earnings", {}), ("sec_filings", "sec-filings", {})):
            try:
                out[t][kind] = ocn(f"/news/market/{path}/{t}", **params)
            except urllib.error.HTTPError as e:
                out[t][kind] = {"error": e.code}  # 404 = OCN does not track this ticker
    out["_indices"], out["_macro"] = ocn("/news/market/indices"), ocn("/news/market/macro")
    save_json("market/anthropic_and_partners.json", out)
    counts["market/tickers"] = {"tracked": sum(1 for t in tickers if "error" not in out[t]["quote"]), "asked": len(tickers)}


def sec(counts: dict) -> None:
    h = {"User-Agent": SEC_UA}
    sub = get(f"https://data.sec.gov/submissions/CIK{CIK}.json", h)
    save_json("anthropic/edgar_submissions.json", sub)
    try:
        save_json("anthropic/edgar_companyfacts.json", get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json", h))
    except urllib.error.HTTPError as e:
        print(f"  companyfacts: HTTP {e.code} (XBRL facts usually appear only after the first 10-Q or 10-K)")
    counts["anthropic/filings"] = len(sub["filings"]["recent"]["form"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only articles published on or after YYYY-MM-DD")
    a = ap.parse_args()
    started, counts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), {}
    print(f"OCN at {BASE}; OCN key {'set' if KEY else 'not set'}; SEC user agent {'set' if SEC_UA else 'not set'}")
    if SEC_UA:
        sec(counts)
    articles(a.since, counts)
    if KEY:
        ocn_keyed(counts)  # after articles: OCN's AI-news grades are matched to the pulled articles
    manifest = OUT / "manifest.json"
    prior = json.loads(manifest.read_text()) if manifest.exists() else {}
    prior.update({"source": BASE, "match": MATCH.pattern, "last_pulled_at": started, "since": a.since,
                  "counts": {**prior.get("counts", {}), **counts}})
    manifest.write_text(json.dumps(prior, indent=2) + "\n")
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
