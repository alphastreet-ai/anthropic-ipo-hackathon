# Market-implied Monte Carlo

- **Author(s):** @tushar-amrit-6
- **Repo:** https://github.com/tushar-amrit-6/anthropic-ipo-hackathon/tree/main/submissions/tushar-amrit-6 (public; a fork of this repo, everything lives in this folder)
- **Method and stack:** Statistical model, Python 3 standard library only. No LLM, no API key, nothing to install. One seeded Monte Carlo (150,000 paths) over a mixture of three estimators.
- **The idea:** Read where ANT will trade from markets that already have money on it, instead of from the first-day pops of deals a fiftieth of Anthropic's size. Two such markets exist for the first time for a mega-IPO: Hyperliquid's per-share pre-IPO perpetual `xyz:ANT`, and Polymarket's bucketed market on Anthropic's day-one closing market cap. Both are pulled by code and cross-checked against a size-, coverage- and recency-weighted paired bootstrap of 17 large IPOs with verified day-1 and day-5 prices. The IPO price itself is a discrete distribution whose key input, P(prices above $78), is pooled from a hot-IPO base rate, likelihood ratios mined from this week's headlines, and Polymarket's proceeds market.
- **How it works:**
  1. `ipo_price`. Base rate: 9 of the 15 comparables that priced at or above the top of their range went above it (60%). Headlines are regex-tagged (above / top-or-above / top-only / hold / raise / below-or-cut) and each signal counts once at its best source tier, so syndicated copies of one wire story do not stack; this week that is CNBC ("likely to price above the current range") and Bloomberg ("top of range or above"), likelihood ratio 3.0. Polymarket's proceeds market gives P(gross proceeds >= $65B), which on 825M shares is P(price >= $78.79). The three are pooled in log-odds. The premium above $78 is a kernel over the premiums the above-range comparables achieved (3-16% of the range top), shrunk by 20% because the four largest comparables all stopped at the top and the $26B Class N placement is bought at the IPO price, and capped at the 20% Rule 430A limit. Mass is also placed at exactly $78, inside the range, and below it.
  2. `day1_close`. A mixture: 40% Polymarket's pooled market-cap CDF (three events, weighted by log volume, converted to a per-share price with the 13.09B post-IPO share count from the S-1/A), 25% the `xyz:ANT` perp mark with a lognormal spread (its own realized vol plus a basis term) and a bias correction of half its only precedent (the SpaceX perp sat 17% above the open and 9% above the first close), 35% the comparables' close/IPO ratio drawn as a paired bootstrap, with each deal's first-day move scaled to a $64B deal along the slope fitted across the hot comparables.
  3. `day1_open`. Paired to the same draw: the comparable's own open/close ratio, or the perp with its open-side bias correction.
  4. `day5_close`. Half the paths carry the day-one close through the comparable's own day-5/day-1 ratio; half re-anchor on the perp as the post-pop level, with a wider spread. Five percent of every quantity passes through a diffuse lognormal for model error, and all spreads scale with the VIX from FRED.
  Medians and the 10th/90th percentiles of the simulated paths are the submission. The thesis and risks are generated from the same numbers.
- **How to rerun:**
  ```bash
  python3 submissions/tushar-amrit-6/run.py              # deterministic: reproduces prediction.json from the snapshots in data/
  python3 submissions/tushar-amrit-6/run.py --refresh    # re-fetch Polymarket, Hyperliquid, Google News RSS and FRED into data/, then predict
  python3 submissions/tushar-amrit-6/run.py --help
  ```
  No keys and no packages beyond the standard library (CI's `jsonschema` is only for `scripts/validate.py`, which the script calls at the end). The default run never touches the network. `--refresh` hits only public, unauthenticated endpoints and keeps the committed snapshot for any source that fails. `--seed` changes the Monte Carlo seed; `params.json` holds every other number.
- **Run log:** the output of the run that produced `prediction.json` is below; the full feature dump (every weight, every headline hit, every market bucket) is in [`run_log.json`](run_log.json).

```
ok   /Users/tamrit/Desktop/anthropic/anthropic-ipo-hackathon/submissions/tushar-amrit-6/prediction.json
1/1 valid

== inputs ==
book coverage (headline median): 6.0x from 27 mentions
  headline[above, tier 1] Anthropic likely to price IPO above the current range of $70 to $78, sources tell CNBC - C (CNBC)
  headline[top_or_above, tier 1] Anthropic Said to Guide IPO Price to Top of Range or Above - Bloomberg.com (Bloomberg.com)
  headline[top_or_above, tier 2] Anthropic is guiding its IPO price to the top of its range — or higher - Quartz (Quartz)
  headline[top_or_above, tier 2] Anthropic Said to Guide IPO Price to $78 or Higher - The Defiant (The Defiant)
  headline[top_or_above, tier 2] Anthropic Is Said To Guide IPO Price To Top Of Range Or Above - Bloomberg - Investing.com (Investing.com)
  headline[top_or_above, tier 2] Anthropic Is Said To Guide IPO Price To Top Of Range Or Above - Bloomberg - Investing.com  (Investing.com Nigeria)
  headline[top_or_above, tier 2] ANTHROPIC IS SAID TO GUIDE IPO PRICE TO TOP OF RANGE OR ABOVE - Moomoo (Moomoo)
  headline[top_or_above, tier 2] Anthropic Is Said To Guide IPO Price To Top Of Range Or Above - Bloomberg - Investing.com  (Investing.com Canada)
  headline[top_or_above, tier 2] Anthropic is said to guide IPO price to top of range or above - Moneycontrol.com (Moneycontrol.com)
  headline[top_or_above, tier 2] Anthropic Is Said To Guide IPO Price To Top Of Range Or Above - Bloomberg By Investing.com (Investing.com India)
  headline[top_only, tier 1] Watch Anthropic Is Said to Guide IPO Price to Top of Range - Bloomberg.com (Bloomberg.com)
  headline[top_only, tier 2] Anthropic Said to Guide IPO Price to High End of Range, Bloomberg Reports - www.marketscre (www.marketscreener.com)
  headline[raise_range, tier 2] Anthropic may raise its IPO price range - Traders Union (Traders Union)
Polymarket: P(proceeds>=65B)=0.66  P(above $78, thin buckets)=0.51  implied day-1 close {'p10': 72.29, 'median': 103.97, 'p90': 134.76}  (cap market vol $9.7M)
Hyperliquid xyz:ANT: mark $97.42  realized daily vol 2.6%  OI $131M
VIX 18.7 -> vol scale 0.93
comps weights: SPCX 0.20, RIVN 0.12, ARM 0.08, BABA 0.07, KLAR 0.06, CBRS 0.06, ABNB 0.06, DASH 0.06
comps-only medians (size-adjusted, slope -0.09): open x1.27 close x1.25 day5/day1 x0.95

== ipo_price model ==
prior(above|hot)=0.60  news LR=2.97  pooled P(above)=0.72  P(top)=0.21  P(within)=0.03  P(below)=0.04

== prediction ==
ipo_price   median   81.75   p10   78.00   p90   86.75
day1_open   median   98.40   p10   75.59   p90  140.36
day1_close  median  100.11   p10   78.17   p90  136.94
day5_close  median   99.49   p10   77.44   p90  140.71
confidence 0.50
wrote /Users/tamrit/Desktop/anthropic/anthropic-ipo-hackathon/submissions/tushar-amrit-6/prediction.json and run_log.json
```

## What each source says on its own

| Source | Day-one close it implies | Notes |
|---|---|---|
| Polymarket closing-market-cap markets (3 events, $9.7M volume) | p10 $72 · median $104 · p90 $135 | Median cap $1.36T on 13.09B shares; the binary "close above IPO price" market sits at 94%, consistent with this CDF |
| Hyperliquid `xyz:ANT` pre-IPO perp | $97.42 mark, $131M open interest | Quoted as one Class A share in USD; converts to the live ANT price after listing. Realized daily vol 2.6% over the last ten sessions |
| 17 comparables, paired bootstrap | 1.25x the IPO price (~$102) | SpaceX, Rivian, Arm, Alibaba, Klarna, Cerebras carry the most weight; no single deal may exceed 20% |
| Mixture (submitted) | median $100.11 · p10 $78 · p90 $137 |  |

## Data and provenance

| File | What | Source |
|---|---|---|
| `data/polymarket.json` | 10 Anthropic IPO events with every market's bucket, price, bid/ask, volume and resolution text | Polymarket Gamma API (public) |
| `data/hyperliquid.json` | `xyz:ANT` asset context (mark, oracle, OI, funding, volume) and daily candles | Hyperliquid info API, dex `xyz` (public) |
| `data/headlines.json` | Headline, source, date, link only, deduplicated; no article text | Google News RSS search |
| `data/fred.json` | VIX and 10-year yield since Aug 2026 | FRED, or the organisers' `context/macro/fred.csv` when FRED is unreachable |
| `data/comps.csv` | 17 IPOs: final range, IPO price, day-1 open and close, day-5 close, reported book coverage, deal size | Yahoo Finance history tables read per row (day-5 closes were verified from the pages, not recalled), 424B4 filings and press for ranges and coverage; each row names its source |
| `context/anthropic-s1/offering.csv` | Range, shares, post-IPO share count | Organisers' pack, with fallbacks in `params.json` |

Point-in-time: market snapshots were taken on 2026-11-17 (UTC), before pricing. Re-running with `--refresh` before the lock updates the market inputs; the comparables and parameters do not change.

## Why these weights

- Markets get 65% of the trading mixture because they are the only inputs that already price Anthropic specifically; the comparables get 35% because two liquid markets with a three-week history are a thin base for tails.
- The perp is haircut by half of the only observed overshoot (SpaceX) rather than the whole of it: one case is not a calibration.
- The premium shrink (20%), the single-comparable cap (20%), the diffuse component (5% of paths at sigma 0.35) and the within-range and below-range masses (3% and 4%) are judgment priors. They are in `params.json` so a judge can move any of them and rerun.

## Limitations I know about

- Polymarket's market-cap buckets become per-share prices through the 13.09B post-IPO share count. If traders are anchoring on the ~18B fully diluted count that the "$1.4T" headlines use, the market-implied close is lower than this reads it. The binary "close above IPO price" market at 94% argues for the 13.09B reading.
- Reported book coverage comes from headline mining (median of 33 mentions, 6x); it is a noisy proxy for the order book.
- The size slope across the comparables is weak (-0.09 per log-unit), so the small hot deals still widen the right tail.
- Nothing here knows what Nvidia says after the close on 17 Nov.
