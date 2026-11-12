#!/usr/bin/env python3
"""Refresh the public parts of context/: macro series, headlines, the filing list. Standard library only, no keys.

  python scripts/fetch_context.py
  SEC_USER_AGENT="Your Name you@example.com" python scripts/fetch_context.py   # also refresh the EDGAR filing list

A source that fails is skipped with a warning; the others still update.
"""
import csv, io, json, os, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

CTX = Path(__file__).resolve().parents[1] / "context"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
FRED = {"DGS10": "10-year Treasury yield", "DGS2": "2-year Treasury yield", "VIXCLS": "VIX", "BAMLH0A0HYM2": "High-yield credit spread"}
QUERIES = ["Anthropic IPO", "Anthropic S-1", "Nvidia earnings"]


def get(url: str, agent: str = "ipo-arena-fetch/1.0") -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": agent}), timeout=30) as r:
        return r.read()


def macro():
    rows = []
    for sid, name in FRED.items():
        text = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2026-08-01").decode()
        for date, value in list(csv.reader(io.StringIO(text)))[1:]:
            if value not in ("", "."):
                rows.append([date, sid, name, value])
    with open(CTX / "macro/fred.csv", "w", newline="") as f:
        csv.writer(f).writerows([["date", "series", "name", "value", f"source=FRED fetched_at={NOW}"], *rows])
    return len(rows)


def news():
    """Headline, link, date and source only. Never article text."""
    out = CTX / "news/headlines.jsonl"
    seen = {json.loads(line)["link"]: json.loads(line) for line in out.read_text().splitlines()} if out.exists() else {}
    for q in QUERIES:
        feed = get("https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f"{q} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"}))
        if b"<!DOCTYPE" in feed or b"<!ENTITY" in feed:  # no entity tricks from a remote feed
            raise RuntimeError("unexpected DTD in news feed")
        for item in ET.fromstring(feed).iter("item"):
            link = item.findtext("link")
            seen.setdefault(link, {
                "published": parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "title": item.findtext("title"), "source": item.findtext("source"), "link": link, "query": q, "fetched_at": NOW})
    rows = sorted(seen.values(), key=lambda r: r["published"], reverse=True)[:400]  # keep the pack small enough for one prompt
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return len(rows)


def filings():
    agent = os.environ.get("SEC_USER_AGENT")
    if not agent:
        raise RuntimeError("set SEC_USER_AGENT to 'Name email' (the SEC requires a contact)")
    recent = json.loads(get("https://data.sec.gov/submissions/CIK0002130357.json", agent))["filings"]["recent"]
    rows = [{"form": f, "filed": d, "url": f"https://www.sec.gov/Archives/edgar/data/2130357/{a.replace('-', '')}/{doc}"}
            for f, d, a, doc in zip(recent["form"], recent["filingDate"], recent["accessionNumber"], recent["primaryDocument"])]
    (CTX / "anthropic-s1/filings.json").write_text(json.dumps({"source": "SEC EDGAR", "fetched_at": NOW, "filings": rows}, indent=2) + "\n")
    return len(rows)


if __name__ == "__main__":
    failed = 0
    for step in (macro, news, filings):
        try:
            print(f"{step.__name__}: {step()} rows")
        except Exception as e:  # one dead source must not stop the daily refresh
            failed += 1
            print(f"{step.__name__}: skipped ({e})", file=sys.stderr)
    sys.exit(1 if failed == 3 else 0)
