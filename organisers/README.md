# Organisers: building the signal pack

Entrants do not need anything in this folder. The pack in `signals/pack/` is built here from AlphaStreet's feed, which only organisers can reach.

```bash
export ALPHASTREET_FEED_URL=...        # the feed's address; never committed
export ALPHASTREET_FEED_KEY=...        # needed for graded signals and market data
python3 organisers/pull_signals.py     # 1. copy matching rows into organisers/raw/ (about 45 minutes for a full scan)
python3 organisers/build_signals.py    # 2. dedup, structure and weight them into signals/pack/ (seconds)
```

`organisers/raw/` is in `.gitignore`. So is `signals/pack/`, until the headlines and summaries are cleared for publishing.

## What is pulled

Anthropic has no ticker until it lists, so news is found by text. An article is kept when either:
- it mentions Anthropic anywhere: Anthropic, Amodei, Claude product names. "Claude" alone is left out because it is also a first name.
- its headline names a company in [`../signals/companies.csv`](../signals/companies.csv).

With the key set, the pull also keeps the feed's graded signal families. It keeps only rows labelled `signal` or `weak_signal` that can move the IPO. A `weak_signal` must name Anthropic or a linked company in its headline, or, for geopolitical rows, have a chip, export-control or AI headline. The rules for each family:

| Family | Detector | Kept when |
|---|---|---|
| Taiwan filings and press | `taiwan` | It names Anthropic or a linked company, by name, ticker or local code (TSMC 2330) |
| Korea filings, exports and press | `korea` | Same; SK Hynix 000660, Samsung 005930 |
| Japan filings and press | `japan` | Same |
| China filings, policy and press | `china` | Same, including the US tickers in a read-through (Alibaba 09988) |
| Middle East | `middle_east` | Same, including the affected companies listed |
| Geopolitical | `geo` | A linked company is named, the headline is about chips, export controls or AI, or (full signals only) the row falls in a chip, compute, cloud, networking, AI-capital or sovereign-AI category |
| Macro | `macro` | The policy-path, discount-rate, credit, liquidity and risk-appetite channels, and level extremes in rate, credit and volatility series |

Those rules are set in `signals/weights.json` under `build.relevance`.

## How the pack is built

1. **Dedup.**
   - One row per canonical URL.
   - Near-duplicate headlines are merged per domain, inside that domain's time window and similarity threshold (`build.dedup`). The merged copies are kept in `also_reported_by`.
   - Stories that appear in more than one domain share a `cluster_id`.
2. **Labels.**
   - The classifier's grade is used where one exists.
   - Otherwise headline rules apply: templates such as share-holding notices, insider filings and price-move lists are noise. A row that names Anthropic only in the body is weak.
   - Noise is left out of the pack.
3. **Impacts.**
   - ANT gets a `direct` row when Anthropic is named.
   - Otherwise ANT gets one row per link type of each named company.
   - Macro, geopolitical and Middle East signals also reach ANT as `macro`.
4. **Weights.** Each signal keeps its raw inputs in `_source.basis`, and `scripts/apply_weights.py` turns them into numbers with `signals/weights.json`. Entrants run the same script after changing weights, without the feed.

`python3 organisers/build_signals.py --selftest` checks dedup, labels, the regional mappings and the weighting.

## Before the repo goes public

- **Licence clearance:** clear the headlines and summaries for publishing, then commit `signals/pack/` by removing it from `.gitignore`.
- **Git history:** it still contains the internal service name, its address and the folder layout from before this structure. Publish from a fresh repository or rewrite history, rather than flipping this one to public.
- **This folder:** decide whether `organisers/` ships with the public repo or moves to a private one.
