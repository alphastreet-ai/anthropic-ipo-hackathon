# Three clocks

- **Author(s):** @Neelabhra08
- **Repo:** https://github.com/Neelabhra08/anthropic-ipo-hackathon/tree/main/submissions/Neelabhra08 (public; a fork of this repo. The code, data, tests and signals are all in this folder; [`README.md`](README.md) lists the files)
- **Method and stack:** A statistical model in plain Python 3, standard library only. No LLM (large language model), no API keys, no installs.
- **The idea:** Do not trust any single reading of where ANT will trade. Three independent "clocks" each say where the first day closes, and a table of 66 past IPOs with verified price ranges says how banks split that level into an IPO price and a first-day gain. The new part is checking the pre-IPO perpetual against every earlier listing that had one, on the same exchange, instead of taking its price at face value.
- **How it works:**
  1. **Perpetual clock.** The pre-IPO perpetual `xyz:ANT` on Hyperliquid gives a live, money-backed level. Three 2026 listings had one before they traded (Cerebras, Quantinuum, SpaceX). For each, the code measures how much of the gain the perpetual expected over the IPO price had arrived by the first close: about 1.2 for Cerebras, 0.8 for SpaceX and 0.0 for Quantinuum, whose stock closed day one at its IPO price while the perpetual sat 60% higher. ANT's first close is drawn between its IPO price and the perpetual in those proportions.
  2. **Market clock.** Prediction-market odds on the first-day closing value are turned into a share-price distribution by fitting a lognormal to the bracket odds.
  3. **History clock.** The first-day gains of the 15 largest past IPOs are applied to the IPO price.
  4. **IPO price.** Past deals show the offer price moves only part of the way from the filed range toward the level the stock then trades at. A kernel regression over the 66 deals gives the IPO price for a given expected level, in whole dollars. Until the day before pricing it uses the range each deal first filed, so the usual late range increases are included. On pricing day, or once the range has been refiled unchanged within a trading day of pricing (as Amendment No. 3 did on 17 Nov), it uses the last filed range and applies the SEC Rule 430A limit (a price more than 20% above the range needs a new filing).
  5. **Open and day five.** The same table gives the spread of the first trade and the fifth close around the first close.
  6. All of it is drawn jointly 40,000 times with a fixed seed, so the four calls belong to one consistent story. The medians and the 10th and 90th percentiles of those draws are the submitted numbers. The thesis text is written by the code from the same numbers.
- **How to rerun:** from the repo root, with any Python 3.10 or newer:

  ```bash
  python submissions/Neelabhra08/run.py
  ```

  This uses the data committed in `data/`, needs no network, and reproduces `prediction.json` exactly. To forecast from live data instead (Hyperliquid and Yahoo chart data, no keys):

  ```bash
  python submissions/Neelabhra08/run.py --refresh
  ```

  Tests: `python -m unittest discover submissions/Neelabhra08/tests`. Backtest: `python submissions/Neelabhra08/backtest.py`.

## Data

| File in `data/` | What it holds | Source |
|:---|:---|:---|
| `deal.json` | Range, share counts, lock-up release, dates, reported book coverage and expected price | Form S-1/A Amendment No. 3 of 17 Nov 2026, read on SEC EDGAR; press reports for the last two |
| `comps.csv` | 69 US IPOs from 2008 to 2026: first range, last range, IPO price, deal size, one source link each | SEC filings and press reports, every row checked against a second lookup |
| `comp_paths.csv` | First trade, first close and fifth close of 66 of them, as ratios to the IPO price | Derived from Yahoo chart data, adjusted back for later stock splits |
| `perp_ant.csv`, `market_snapshot.json` | Daily closes and the latest quote of the ANT perpetual | Hyperliquid public API |
| `perp_calibration.csv` | The Cerebras, Quantinuum and SpaceX perpetuals in their last 7 days before listing, against the real first week | Hyperliquid and Yahoo |
| `prediction_market.json` | Bracket odds for the first-day closing value | Recorded from a public report with its link, because the Polymarket API was not reachable from the build network |

Three deals in `comps.csv` have no price path because the vendor lacks their first day (Kenvue, Klarna, INNIO). Stock prices are stored only as ratios.

## Backtest

`backtest.py` replays the method on the three earlier listings, walking forward in time. Each replay sees only what was known four days before the first trade. Scores use `scoring/score.py`'s formula.

| Listing | IPO price | Day-1 open | Day-1 close | Day-5 close |
|:---|---:|---:|---:|---:|
| Cerebras (no earlier perpetual to learn from) | 0.67 | 0.75 | 0.84 | 0.91 |
| Quantinuum | 1.00 | 0.45 | 0.00 | 0.00 |
| SpaceX (fixed price, so no IPO price call) | n/a | 0.89 | 0.99 | 0.89 |

The mean is 0.67 over 11 calls. Quantinuum is the failure that shaped the model: its perpetual was froth, and the stock closed at its IPO price. That case is why the perpetual clock can land on the IPO price, why the other two clocks exist, and why it is the second key risk.

## Signals and weights

`run.py` writes the three readings behind the forecast to [`signals/neelabhra08.signals.jsonl`](signals/neelabhra08.signals.jsonl) in the `signal.v1` format: the perpetual, the prediction-market odds, and the filing term that lets up to 315 million employee shares sell from the first trading day. They sit in this folder because a submission may only change its own folder; copied to `signals/custom/` at the repo root, `scripts/apply_weights.py` merges them into the pack. `signals/weights.json` is unchanged. The signal pack was not in the repo when this entry was built (`signals/pack/` is git-ignored), so this entry weights its sources by their measured record instead: the perpetual's reliability is the share of earlier listings where it held up, and the three clocks are weighted equally because three earlier listings are too few to estimate anything finer.

## Limits

- Three earlier listings is a small sample for the perpetual clock.
- The prediction-market odds are a snapshot typed in from a cited report, not a live feed.
- The model sees Nvidia's results of 17 Nov only through the perpetual's price. The submitted run was made after they were released.
- The model does not use press reports of the expected price. The Wall Street Journal reported on 17 Nov that the price is expected above $80. That agrees with the median call, but the model still gives a sizeable chance to a price at or below the top of the range.
- If Anthropic files a new range, `last_range` and `last_range_filed_on` in `data/deal.json` must be updated by hand before the rerun.

## Run log

The run that produced `prediction.json` is in [`run_log.txt`](run_log.txt).

Last updated: 2026-11-18
