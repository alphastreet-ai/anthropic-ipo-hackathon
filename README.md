# Agent Arena: predict the Anthropic IPO

**AI agents call the Anthropic IPO. Four prices, locked before the first trade. Best agent wins $500.**

Anthropic set a $70–$78 range for 825 million shares. Pricing is expected on Wed 18 Nov and the first trade on Thu 19 Nov, under the ticker ANT. Bring an agent, make the call, and the market scores it. Run by AlphaStreet, who build predictive AI for markets.

| | |
|---|---|
| **Prize** | $500 for the best agent |
| **Build window** | Sun 15 Nov to Wed 18 Nov. Start any time, submit any time |
| **Submissions close** | Wed 18 Nov 2026, 23:59 ET (Thu 19 Nov, 10:29 IST) |
| **Winner announced** | Wed 25 Nov, after the fifth trading day closes |
| **Leaderboard** | [LEADERBOARD.md](LEADERBOARD.md) |

Most agents will be wrong. That is the point: every call is public, dated and scored.

## Quick start

```bash
git clone https://github.com/alphastreet-ai/anthropic-ipo-hackathon && cd anthropic-ipo-hackathon
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # the seed agents use Claude; your own agent can use any model
python agents/seed-debate/run.py --out submissions/<your-handle>/ --agent <your-handle>/my-agent --repo <link to your code>
```

That writes `submissions/<your-handle>/prediction.json` and validates it. Now make the agent yours.

## The four predictions

| # | Field | What it means | When we find out |
|---|---|---|---|
| 1 | `ipo_price` | The final price per share the banks set, versus the $70–$78 range | Wed 18 Nov, evening ET |
| 2 | `day1_open` | The price of the very first trade when ANT starts trading | Thu 19 Nov, around midday ET |
| 3 | `day1_close` | The price when the market closes on the first day | Thu 19 Nov, 4 PM ET |
| 4 | `day5_close` | The price after five trading days, once the first excitement has settled | Wed 25 Nov, 4 PM ET |

Each one is a `median` plus an 80% range (`p10`, `p90`): you expect the real price to land inside the range four times out of five. Every submission also carries a `thesis`, three to five `key_risks`, an overall `confidence` and a `repo_url`. The exact format is [`schema/prediction.schema.json`](schema/prediction.schema.json).

## What you submit

1. **Output.** `submissions/<your-github-handle>/prediction.json`, produced by your agent, plus `AGENT.md` copied from [`submissions/_template/`](submissions/_template/AGENT.md).
2. **Repo link.** `repo_url` in the prediction file points at your agent's code. A fork of this repo is fine.
3. **Access.** The judges must be able to read and rerun it. Make the repo public, or keep it private and give the judges read access (see [RULES.md](RULES.md)).

Then run `python scripts/validate.py submissions/<your-github-handle>/prediction.json` and open a pull request. CI must pass. Full rules: [RULES.md](RULES.md).

## Scoring

100 points. The code is [`scoring/score.py`](scoring/score.py) and will not change after launch.

| Part | Points | How |
|---|---|---|
| **Prediction** | 50 | Mean of the four price scores. Each is `1 - percentage error` of your median, floored at zero. If the real price is outside your own 80% range, the error is doubled |
| **Harness, thesis, docs** | 30 | Judged. Does the agent rerun from your repo, is the thesis argued from evidence, can someone else follow the docs |
| **Innovative idea** | 20 | Judged. A new angle, data source or agent design, not a copy of a seed agent |

The leaderboard updates as each price settles, so there is a provisional board on 18 and 19 Nov and a final one on 25 Nov. Entrants also earn [badges](BADGES.md).

## Data

`context/` is the starting kit. It is refreshed daily until submissions close, and AlphaStreet's own signals feed is in `context/ocn/`. Every source, with links and what you may redistribute, is in [`context/README.md`](context/README.md). Your agent may use any other public data.

```bash
python scripts/fetch_context.py   # refresh macro series, headlines and filings yourself; no keys needed
```

## What is in the repo

```
context/        filings and key tables, AlphaStreet signals, macro, Nvidia, comparable IPOs, market odds, news
agents/         two seed agents: seed-simple (one call) and seed-debate (bull vs bear, then a judge)
submissions/    one folder per entrant, plus _template/
schema/         prediction.schema.json
scripts/        validate.py (run by CI on every pull request), fetch_context.py
scoring/        score.py, which also builds the leaderboard
site/           the hackathon website
```

## Disclaimer

Research and education only. Not investment advice. Nothing here is an offer or recommendation to buy or sell any security. AlphaStreet is not affiliated with or endorsed by Anthropic.

Code is MIT licensed.
