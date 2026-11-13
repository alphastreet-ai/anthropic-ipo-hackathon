#!/usr/bin/env python3
"""Pull Anthropic-related data from OCN into context/ocn/raw/, as-is. Standard library only.

  python scripts/pull_ocn.py                                   # every OCN article that mentions Anthropic
  OCN_API_KEY=... python scripts/pull_ocn.py                   # + OCN signals about Anthropic, market data for ANT and its partners
  SEC_USER_AGENT="Name email" python scripts/pull_ocn.py       # + Anthropic's own company data from SEC EDGAR
  python scripts/pull_ocn.py --since 2026-10-01                # only articles published on or after a date

OCN does not track Anthropic as a company (no ticker until 19 Nov), so its articles are found by text.
Each OCN domain is walked with bodies and a row is kept when either:
  - it mentions Anthropic anywhere (MATCH); the matched words go in `_match`, or
  - its headline names a company linked to Anthropic in context/markets/links.csv (competitor, supplier,
    customer, peer, sector), or Alpha Vantage tags that company's ticker with relevance >= 0.5; these go in `_links`.
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
        except (urllib.error.URLError, TimeoutError):
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
BY_TICKER = {l["ticker"]: l for l in LINKS if l["ticker"]} | {"GOOG": next(l for l in LINKS if l["ticker"] == "GOOGL")}


def links(row: dict) -> list[dict]:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    found = {l["issuer_id"]: {"issuer_id": l["issuer_id"], "link_type": l["link_type"], "via": "title"}
             for l in LINKS if l["re"].search(row.get("title") or "")}
    for t in meta.get("ticker_sentiment") or []:  # company_news: Alpha Vantage tags tickers with a relevance score
        l = BY_TICKER.get(t.get("ticker"))
        if l and float(t.get("relevance_score") or 0) >= 0.5:
            found.setdefault(l["issuer_id"], {"issuer_id": l["issuer_id"], "link_type": l["link_type"], "via": "ticker_sentiment"})
    return sorted(found.values(), key=lambda x: x["issuer_id"])


def matches(row: dict) -> dict | None:
    text = " ".join(str(row.get(k) or "") for k in ("title", "summary", "body", "text", "headline")) + " " + json.dumps(row.get("metadata") or {}, ensure_ascii=False)
    terms, linked = sorted({m.lower() for m in MATCH.findall(text)}), links(row)
    return {"_match": terms, "_links": linked} if terms or linked else None


def save_json(name: str, data) -> None:
    (OUT / name).parent.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def save_matching(name: str, path: str, key: str, **params) -> tuple[int, int] | None:
    """Walk a cursor-paginated endpoint, keep only Anthropic matches. Writes .part, renames when complete."""
    final = OUT / name
    if final.exists():
        print(f"  {name}: already pulled, skipping")
        return None
    final.parent.mkdir(parents=True, exist_ok=True)
    part, seen, kept, cursor = final.with_name(final.name + ".part"), 0, 0, None
    with gzip.open(part, "wt", encoding="utf-8") as f:
        while True:
            page = ocn(path, cursor=cursor, **params)
            for row in page[key]:
                if hit := matches(row):
                    f.write(json.dumps({**row, **hit}, ensure_ascii=False) + "\n")
                    kept += 1
            seen += len(page[key])
            cursor = page.get("next_cursor")
            print(f"\r  {name}: {kept} kept of {seen}", end="", flush=True)
            if not cursor or not page[key]:
                break
    part.rename(final)
    print()
    return kept, seen


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


def ocn_keyed(counts: dict) -> None:
    if r := save_matching("signals/results.jsonl.gz", "/agent/results", "results", limit=500, view="rows"):
        counts["signals/results"] = {"kept": r[0], "scanned": r[1]}
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
    if KEY:
        ocn_keyed(counts)
    articles(a.since, counts)
    manifest = OUT / "manifest.json"
    prior = json.loads(manifest.read_text()) if manifest.exists() else {}
    prior.update({"source": BASE, "match": MATCH.pattern, "last_pulled_at": started, "since": a.since,
                  "counts": {**prior.get("counts", {}), **counts}})
    manifest.write_text(json.dumps(prior, indent=2) + "\n")
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
