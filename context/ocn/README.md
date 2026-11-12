# AlphaStreet signals feed (OCN)

AlphaStreet's own market data and news, pulled from OCN. Refreshed daily until submissions close on Wed 18 Nov.

## Raw pull (internal, not committed)

`python scripts/pull_ocn.py` copies Anthropic-related data from OCN into `raw/`, exactly as OCN returns it. OCN does not track Anthropic as a company yet, so articles are found by text (Anthropic, Amodei, Claude product names); the matched words are kept in `_match`. Nothing else is reshaped or deduplicated here; OCN has already deduplicated articles by URL and folded near-duplicates into `metadata.also_reported_by`.

| File | What it is | Needs |
|---|---|---|
| `raw/domains.json` | OCN's domains | Nothing |
| `raw/articles/<domain>.jsonl.gz` | Every article in that domain that mentions Anthropic, with body, source and metadata | Nothing |
| `raw/signals/results.jsonl.gz` | OCN's own classified signals that mention Anthropic | `OCN_API_KEY` |
| `raw/market/anthropic_and_partners.json` | Quote, 260 days of prices, overview, earnings and SEC filings for ANT and the partner tickers OCN tracks | `OCN_API_KEY` |
| `raw/anthropic/edgar_*.json` | Anthropic's filing list and XBRL company facts from SEC EDGAR | `SEC_USER_AGENT` |
| `raw/manifest.json` | When it was pulled, from where, the match pattern, and counts | |

`raw/` is in `.gitignore`. Article bodies come from licensed and copyrighted sources, and this repo goes public, so only a reshaped, cleared pack belongs in git. That pack is decided next.

This folder holds outputs only. How the signals are produced is not part of the hackathon.
