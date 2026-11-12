#!/usr/bin/env python3
"""Pull Anthropic-related data from OCN into context/ocn/raw/, as-is. Standard library only.

  python scripts/pull_ocn.py                                   # every OCN article that mentions Anthropic
  OCN_API_KEY=... python scripts/pull_ocn.py                   # + OCN signals about Anthropic, market data for ANT and its partners
  SEC_USER_AGENT="Name email" python scripts/pull_ocn.py       # + Anthropic's own company data from SEC EDGAR
  python scripts/pull_ocn.py --since 2026-10-01                # only articles published on or after a date

OCN does not track Anthropic as a company (no ticker until 19 Nov), so its articles are found by text:
each OCN domain is walked with bodies and only rows matching MATCH are kept, with the matched words noted
in `_match`. Rows are otherwise stored exactly as OCN returns them; OCN has already deduped by URL and
folded near-duplicates into metadata.also_reported_by. Structure and further dedup come later.

A finished file is skipped on the next run; delete it to pull it again.
"""
import argparse, csv, gzip, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
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


def matches(row: dict) -> list[str]:
    text = " ".join(str(row.get(k) or "") for k in ("title", "summary", "body", "text", "headline")) + " " + json.dumps(row.get("metadata") or {}, ensure_ascii=False)
    return sorted({m.lower() for m in MATCH.findall(text)})


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
                    f.write(json.dumps({**row, "_match": hit}, ensure_ascii=False) + "\n")
                    kept += 1
            seen += len(page[key])
            cursor = page.get("next_cursor")
            print(f"\r  {name}: {kept} kept of {seen}", end="", flush=True)
            if not cursor or not page[key]:
                break
    part.rename(final)
    print()
    return kept, seen


def articles(since: str | None, counts: dict) -> None:
    domains = ocn("/news/domains")
    save_json("domains.json", domains)
    for d in domains:
        if r := save_matching(f"articles/{d['slug']}.jsonl.gz", "/news/articles", "articles", domain=d["slug"], limit=100, include_body="true", from_date=since):
            counts[f"articles/{d['slug']}"] = {"kept": r[0], "scanned": r[1]}


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
