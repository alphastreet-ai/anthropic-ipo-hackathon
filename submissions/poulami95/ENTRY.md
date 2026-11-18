# Market triangulation: book, perp and Polymarket in one Monte Carlo

- **Author(s):** @poulami95
- **Repo:** https://github.com/poulami95/anthropic-ipo-hackathon (all code is in `submissions/poulami95/`)
- **Method and stack:** Statistical model. A 200,000-path Monte Carlo in Python, standard library only. No LLM and no API key.
- **The idea:** Before the first trade, three money-backed markets already price ANT, and each one is strongest at a different horizon. The order book sets the IPO price. Hyperliquid's xyz:ANT pre-IPO perpetual gives an *absolute* anchor for the open, so a higher IPO price shrinks the pop instead of lifting the open. Polymarket's first-day market-cap brackets give a full distribution for the close. The model simulates joint paths from IPO price to day 5, then **exponentially tilts those paths toward the Polymarket brackets**. That keeps all four calls consistent with each other and with the crowd. The examples model every price as a percentage pop on the IPO price; this entry does not.
- **How it works:**
  1. **IPO price.** A mixture: 5% below $78 (if the book weakens), 30% exactly $78, 65% above. Above-range raises are lognormal with a 6.5% median, capped so banks leave at least 8% headroom under the perp. This follows the 17 Nov guidance to "top of range or above" and a ~7x book.
  2. **Day-1 open.** A log-space blend: 60% the perp ($96.80, less a 3% offshore-premium discount, with fat-tailed noise) and 40% the simulated IPO price times a SpaceX-style pop (+13%). The open has a soft floor at 97% of the IPO price.
  3. **Day-1 close.** The open moved by the median and MAD of open-to-close moves in `data/comps.csv` (SpaceX, Facebook, Alibaba, Uber, Rivian, Arm), less a drag for the up-to-316M-share first-day lock-up release. Paths are then re-weighted by (Polymarket probability / model probability)^0.6 for each close bracket. Brackets convert to price using 13.09B shares outstanding.
  4. **Day-5 close.** Comps' close-to-day-5 drift and spread, a further lock-up drag, and greenshoe support that absorbs 40% of any shortfall below the IPO price.
  5. **Volatility and range.** Volatility is scaled by the latest VIX in `context/macro/fred.csv`. The p10/p90 log-distance from the median is widened 15%, because pre-trade markets tend to be under-dispersed and a miss doubles the error.
  6. **Signal pack.** If `signals/pack/` is present, a point-in-time, decay-weighted and story-deduplicated sentiment score over ANT-linked rows tilts the open by at most ±3%. Rival good news counts as negative. Without the pack, the tilt is 0 and the run log says so.
- **Data:** `data/evidence.json` holds every external number with its source URL and date: the book, guidance, perp history and Polymarket brackets. The repo's `context/` supplies offering terms, VIX and the SpaceX comp. `data/comps.csv` holds five older mega-IPOs taken from public price history and marked `verified=no`; `fetch_live.py --verify-comps` checks them against Yahoo.
- **How to rerun:** `python3 submissions/poulami95/forecast.py` from the repo root. No keys, no installs, fixed seed, and it rebuilds `prediction.json` byte-for-byte. It also rebuilds `report.html` and `run_log.txt`, which carry a run timestamp. To refresh the market inputs first, run `python3 submissions/poulami95/fetch_live.py` (Hyperliquid and Polymarket public APIs, plus NVDA via yfinance if installed), commit `data/evidence.json`, then rerun `forecast.py`.
- **Run log:** [`run_log.txt`](run_log.txt). Chart: [`report.html`](report.html).

Research and education only. Not investment advice.
