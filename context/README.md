# Context pack

Background for your model or agent: the filing, comparable IPOs, macro series, tickers, headlines and dates. The signals themselves are in [`../signals/`](../signals/README.md). It is refreshed daily until submissions close on Wed 18 Nov. You may use any other public data too.

The example agents read every `.md`, `.txt`, `.csv`, `.json` and `.jsonl` file under `context/` (except `README.md` files).

## What is here

| Folder | What is in it | Refreshed |
|---|---|---|
| `anthropic-s1/` | Links to every filing, offering terms, quarterly results, compute commitments, valuation history | When a new filing lands |
| `macro/` | Treasury yields, VIX and credit spreads from FRED | Daily |
| `nvidia/` | Nvidia earnings summary and guidance | Tue 17 Nov, after the call |
| `comps/` | SpaceX and other large tech IPOs: IPO price, open, day-1 and day-5 close | Static |
| `markets/` | Peer and partner tickers, prediction-market and pre-IPO odds | Daily |
| `news/` | Headlines with source, link and time. No article text | Daily |
| `calendar.csv` | The dates that matter between now and the last scored close | Static |

Run `python scripts/fetch_context.py` to refresh `macro/`, `news/` and the filing list yourself. It needs no API keys.

## What moves each prediction

| Prediction | What actually moves it | Look in |
|---|---|---|
| IPO price | Order-book demand, valuation against peers, AI-sector mood in the week before pricing | `anthropic-s1/`, `markets/`, `news/` |
| Day-1 open | Demand against shares actually allocated, the size of the retail slice, overnight futures | `comps/`, `markets/`, `news/`, `macro/` |
| Day-1 close | Whether buyers keep coming after the pop, how AI stocks trade that day, the Nvidia reaction | `markets/`, `nvidia/`, `news/` |
| Day-5 close | Selling by allocated holders, underwriter price support, the wider market | `comps/`, `macro/`, `news/` |

All four hang on one number, the final IPO price. Model day one and day five as a percentage move from it.

## Sources you can pull yourself

| Group | Source | Cost | Notes |
|---|---|---|---|
| Filings | [SEC EDGAR, CIK 2130357](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=2130357) and `data.sec.gov/submissions/CIK0002130357.json` | Free | Send a `User-Agent` with a contact email; 10 requests a second at most |
| Filings | [Anthropic's IPO site](https://ipo.anthropic.com) | Free | Prospectus and roadshow material |
| Macro | [FRED](https://fred.stlouisfed.org): `DGS10`, `DGS2`, `VIXCLS`, `BAMLH0A0HYM2` | Free | Public domain |
| Macro | CME FedWatch, [BLS release calendar](https://www.bls.gov/schedule/news_release/) | Free | Next Fed decision is 8–9 Dec, after scoring ends |
| Prices | Stooq or `yfinance` for daily prices of the tickers in `markets/tickers.csv` | Free | Redistribute derived numbers only, not raw vendor data |
| Deal demand | Polymarket and Kalshi Anthropic IPO markets; pre-IPO perpetual futures (Hyperliquid `xyz:ANT`) | Free to view | A live, money-backed guess at where shares trade |
| News | Google News RSS: `news.google.com/rss/search?q=Anthropic+IPO+when:1d` | Free | Headlines and links |
| News | [GDELT DOC 2.0](https://api.gdeltproject.org/api/v2/doc/doc?query=anthropic&mode=ArtList&format=json&timespan=7d) | Free | Also gives coverage volume and tone over time |
| Social | Hacker News (`hn.algolia.com/api/v1/search_by_date?query=anthropic`), Reddit search RSS | Free | Store counts and tone, not posts |
| Comps | [Jay Ritter's IPO data](https://site.warrington.ufl.edu/ritter/ipo-data/) | Free | First-day returns since 1980 |

## Rules for what goes in this folder

- SEC filings and FRED, BLS and Fed data: full text and values, with the source.
- Prices: derived numbers only (returns, summaries).
- News: headline, link, date and source. Never article text.
- Social: counts and tone only. No copied posts or usernames.
- AlphaStreet signals: only the pack in `signals/pack/`.

Every data file names its `source`. Numbers in `anthropic-s1/*.csv` come from the filings or from coverage quoting them; if a number matters to your thesis, check it against the filing itself.
