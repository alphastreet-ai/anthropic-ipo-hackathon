# Rules

## Dates (all times US Eastern)

| When | What |
|---|---|
| Sun 15 Nov | Build window opens. Data pack is live |
| Tue 17 Nov, after the close | Nvidia reports. `context/nvidia/` is updated that evening |
| Wed 18 Nov, evening | Anthropic's IPO price is expected |
| **Wed 18 Nov, 23:59** | **Submissions close** |
| Thu 19 Nov | First trading day |
| Wed 25 Nov, 4 PM | Fifth trading day closes. Final scores and winner |

If the IPO moves, the closing time and the scoring days move with it and we post the new dates here.

## Who can enter

Anyone, alone or as a team. One submission per person or team. AlphaStreet staff can enter but cannot win.

## What counts as a submission

A pull request that adds `submissions/<your-github-handle>/` with:

- `prediction.json` that passes `scripts/validate.py`
- `AGENT.md`, filled in
- a `repo_url` the judges can open. Public is simplest. If the repo is private, give read access to the judges named in the pinned issue "Judges and access" before submissions close, and make it public before the winner is announced

The pull request may only change files in your own folder.

## Fair play

- **An agent makes the calls.** Do not type the numbers in by hand. The judges rerun agents from the linked repo.
- **GitHub's clock is the clock.** You can push updates until submissions close. Commits after 23:59 ET on 18 Nov are ignored.
- **Prediction 1 must be in before the price is public.** The IPO price is expected a few hours before submissions close. A pull request opened or changed after Anthropic announces the price scores zero on `ipo_price`; its other three calls count as normal.
- Any model, any public data, any architecture. No inside information.
- Submissions are released under the MIT licence.

## Scoring

See the README. Prediction 50, harness, thesis and docs 30, innovative idea 20. The judges are AlphaStreet engineers; the rubric is:

| Judged part | Full marks means |
|---|---|
| Harness (10) | A stranger can clone the repo and reproduce a prediction file with one documented command |
| Thesis (10) | A clear view argued from the filing and the data, with risks that are specific to this deal |
| Docs (10) | `AGENT.md` explains what the agent reads, how it reasons and how it turns that into prices |
| Innovative idea (20) | An approach the seed agents do not take: a new data source, a new agent design or a sharper question |

Ties go to the higher judged score, then to the earlier pull request.

## Prize

$500 to the top total score, paid within 14 days of the announcement by bank transfer or PayPal. The winner is responsible for any tax. If there is no pricing by 31 Dec 2026, the prize is decided on the judged points only.

## Disclaimer

Research and education only. Not investment advice. AlphaStreet is not affiliated with or endorsed by Anthropic.
