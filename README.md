# Anthropic IPO Agent Hackathon

**Bring your agent, predict the Anthropic IPO, win $500.**

Build an agent that reads the filing and the wider AI picture, then commits to seven dated calls. The most accurate agent wins. Run by AlphaStreet, who build predictive AI for markets.

| | |
|---|---|
| **Prize** | $500, one winner |
| **Predictions lock** | Wed 18 Nov 2026, 23:59 IST |
| **Submit** | A pull request to this repo |
| **Winner announced** | Tue 1 Dec 2026 |

## Quick start

```bash
git clone <this repo> && cd anthropic-ipo-hackathon
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # the seed agents use Claude; your own agent can use any model
python agents/seed-simple/run.py --out submissions/<your-handle>/ --agent <your-handle>/my-agent
```

That writes `submissions/<your-handle>/prediction.json` and validates it. Now make the agent yours.

## The seven calls

| ID | Your agent predicts | Answer as | Settles |
|---|---|---|---|
| `q1_ipo_price` | Final IPO price per share | median + 80% range | At pricing |
| `q2_above_range` | Priced above the top of the last filed range | probability | At pricing |
| `q3_upsized` | Share count raised before pricing | probability | At pricing |
| `q4_day1_open` | Opening price, first trading day | median + 80% range | Day one |
| `q5_day1_close` | Closing price, first trading day | median + 80% range | Day one |
| `q6_close_above` | Day-one close above the IPO price | probability | Day one |
| `q7_close_nov30` | Closing price on Mon 30 Nov | median + 80% range | 30 Nov |

An 80% range is `p10` and `p90`: you expect the real number to land inside it four times out of five. Every submission also carries a `thesis`, three to five `key_risks` and an overall `confidence`. The exact format is [`schema/prediction.schema.json`](schema/prediction.schema.json).

## How to submit

1. Fork this repo.
2. Put your files in `submissions/<your-github-handle>/`:
   - `prediction.json`, produced by your agent
   - `AGENT.md`, copied from [`submissions/_template/AGENT.md`](submissions/_template/AGENT.md)
   - your agent's code, or a link to it in `AGENT.md`
3. Run `python scripts/validate.py submissions/<your-github-handle>/prediction.json`.
4. Open a pull request before the lock. CI must pass.

You can push updates until the lock. At the lock we tag the repo; any edit after the tag disqualifies the submission.

## Rules

- One submission per person or team. Anyone can enter.
- Any model, any public data, any architecture. An agent has to produce the numbers; do not type them in by hand.
- A pull request may only change files in your own `submissions/<handle>/` folder.
- If the IPO slips, calls settle on the real first trading day. If there is no pricing by 31 Dec 2026, the prize is decided on judged points only.

## Scoring

100 points. The code is in [`scoring/score.py`](scoring/score.py) and will not change after launch.

- **Accuracy, 70 points.** The mean of seven per-question scores:
  - probability calls: `1 - (p - outcome)^2` (Brier score)
  - price calls: `1 - percentage error` of your median, floored at zero. If the real price is outside your own 80% range, the error is doubled.
- **Judged, 30 points.** Thesis quality, the risks you named, whether the agent reruns cleanly, and originality.
- **Ties** go to the earlier pull request.

## What is in the repo

```
context/        the filing, AlphaStreet macro signals, Nvidia, comparable IPOs, market odds
agents/         two seed agents: seed-simple (one call) and seed-debate (bull vs bear, then a judge)
submissions/    one folder per entrant, plus _template/
schema/         prediction.schema.json
scripts/        validate.py, also run by CI on every pull request
scoring/        score.py
site/           the hackathon landing page
backend/        registration form backend (Google Apps Script)
```

## Disclaimer

This is a forecasting contest, not investment advice. Nothing here is an offer or recommendation to buy or sell any security. AlphaStreet is not affiliated with or endorsed by Anthropic.

Code is MIT licensed.
