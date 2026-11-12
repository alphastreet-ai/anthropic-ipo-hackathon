# AlphaStreet signals feed (OCN)

AlphaStreet's own market data and news, pulled from OCN. Refreshed daily until submissions close on Wed 18 Nov.

## Raw pull (internal, not committed)

`python scripts/pull_ocn.py` copies what OCN serves into `raw/`, exactly as OCN returns it. Nothing is reshaped or deduplicated here; OCN has already deduplicated articles by URL and folded near-duplicates into `metadata.also_reported_by`.

| File | What it is | Needs `OCN_API_KEY` |
|---|---|---|
| `raw/domains.json`, `raw/sources.json`, `raw/frequencies.json` | OCN's domains and the sources behind each | No |
| `raw/runs.jsonl.gz` | Every fetch run: domain, time, article count | No |
| `raw/articles/<domain>.jsonl.gz` | Every article in that domain, with body, source and metadata | No |
| `raw/market/*` | Tracked tickers, quotes, 260 days of prices, overview, earnings, SEC filings, indices | Yes |
| `raw/macro/*` | The macro series list and every observation | Yes |
| `raw/signals/results.jsonl.gz` | OCN's own classified signals, every stored row | Yes |
| `raw/manifest.json` | When it was pulled, from where, and row counts | |

`raw/` is in `.gitignore`. Article bodies come from licensed and copyrighted sources, and this repo goes public, so only a reshaped, cleared pack belongs in git. That pack is decided next.

This folder holds outputs only. How the signals are produced is not part of the hackathon.
