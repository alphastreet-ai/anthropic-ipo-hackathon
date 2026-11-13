# The signal pack

About 12,000 dated signals that can move the Anthropic IPO. Each is mapped to the companies it touches and to how it reaches ANT. Use the pack as it is, re-weight it, or add your own signals, then turn it into the four prices.

| File | What it is |
|---|---|
| `pack/signals.v1.jsonl` | One signal per line, each event once, newest first |
| `pack/signal_impacts.v1.jsonl` | One line per signal and company it reaches, joined to signals on `signal_id` |
| `pack/stats.json` | Counts by detector, label and link |
| [`weights.json`](weights.json) | Every number you can tune |
| [`custom/`](custom/README.md) | Your own signals |
| [`companies.csv`](companies.csv) | The 48 companies linked to Anthropic, and how |
| [`signal.v1.schema.json`](signal.v1.schema.json) | The format of both files |
| `explorer/` | Two pages for browsing the pack |

The pack is refreshed daily until submissions close on Wed 18 Nov.

## Read it

```python
import json
signals = [json.loads(l) for l in open("signals/pack/signals.v1.jsonl")]
impacts = [json.loads(l) for l in open("signals/pack/signal_impacts.v1.jsonl")]
on_ant = [i for i in impacts if i["issuer_id"] == "ANT"]   # how each signal reaches ANT
```

Or browse it. From the repo root run `python3 -m http.server 8000`, then open:
- `http://localhost:8000/signals/explorer/signals.html`: every signal, with filters; a row shows every field and the companies it reaches.
- `http://localhost:8000/signals/explorer/impacts.html`: every signal-to-company link, the companies hit most, and filters by link, direction and confidence.

## What a signal is

A signal is one event that can move a price: a news story, an exchange filing, a Gulf or geopolitical event, or a move in rates or credit. It is recorded once, with when it happened, when it was published and when it could first have been known.

| Detector | What it covers | Signals |
|---|---|---|
| `news` | AI, company, markets and VC news that names Anthropic or a linked company | ~11,100 |
| `geo` | Chip, export-control and AI policy, sanctions and trade | ~640 |
| `macro` | Treasury, real-rate, credit, liquidity and volatility moves and extremes | ~130 |
| `middle_east` | Gulf AI deals and attacks, with the companies they hit | ~40 |
| `taiwan`, `korea`, `japan`, `china` | Exchange filings and press about linked companies (TSMC, SK Hynix, Samsung, SoftBank, Alibaba…) | ~35 |

## How a signal reaches ANT

The impacts file has one row per signal and company. Every signal reaches ANT one or more ways:

| `link_type` | Meaning | Default `link_confidence` |
|---|---|---|
| `direct` | Anthropic is named: in the headline, or only in the body | 1.0, or 0.5 |
| `supplier` | A supplier is named: chips, cloud, memory, data centres, power (Nvidia, TSMC, Amazon…) | 0.8 |
| `competitor` | A rival is named (OpenAI, xAI, Meta, DeepSeek…) | 0.6 |
| `customer` | A buyer or reseller of Claude is named (Salesforce, Palantir…) | 0.5 |
| `peer`, `sector` | An AI-capex peer or AI infrastructure company is named | 0.4 |
| `macro` | Rates, credit or geopolitics, which move every growth stock | 0.3 |

The named companies get their own `direct` rows too, with `direction` (+1 rises, −1 falls, 0 neutral) where the source gives one. One company can link more than one way: Google is both a supplier and a competitor. `companies.csv` lists every linked company. Private companies (OpenAI, Mistral…) have no ticker.

## The fields

**Signals** (`signals.v1.jsonl`):

| Field | Meaning |
|---|---|
| `signal_id`, `cluster_id` | The signal, and the story it belongs to: reports of one event share a cluster, so it counts once |
| `detector`, `subtype`, `scope` | Which family it came from, and whether it is about one `company`, several (`sector`), `macro`, `geopolitical` or `policy` |
| `signal_detection` | `signal` or `weak_signal`. Noise is already removed |
| `title`, `title_original`, `language`, `summary`, `evidence` | What happened, and why it was labelled the way it was |
| `source_kind`, `source_name`, `source_url`, `source_reliability` | Where it came from, and how much to trust it (0–1) |
| `subject_issuer_id`, `ticker`, `region`, `country` | Who it is about and where |
| `event_direction` | Good (+1) or bad (−1) for the subject, where known; often `null` |
| `strength`, `confidence`, `materiality`, `novelty` | How big, how sure, how much it matters, how new (each 0–1, or `null` where unknown) |
| `category`, `half_life_days`, `horizon_days` | Topics (ipo, funding, supply, regulation…), and how long the signal keeps counting |
| `event_at`, `published_at`, `detected_at`, `valid_until` | When it happened, was published, could first be known, and stops counting |
| `signal_type`, `superseded_by`, `superseded_at` | `event` (fades with age) or `state` (holds until replaced, such as quarterly results) |
| `backfilled` | Found more than a week after it was published |
| `details` | Fields particular to the detector: macro `series_id`, `z_score`, `move_bp`; Middle East `affected_companies`, `effect`; geo `channel`, `assets`… |
| `_source` | Provenance, and `basis`: the raw inputs the weighted numbers are computed from |

**Impacts** (`signal_impacts.v1.jsonl`): `signal_id`, `issuer_id`, `link_type`, `link_hops` (0 named, 1 through a link), `direction`, `exposure` (not measured yet, always `null`), `exposure_source`, `link_confidence`.

**Point-in-time:** to stay honest, use only signals whose `detected_at` is before the moment you predict for.

## Change the weights

[`weights.json`](weights.json) holds every number the pack is weighted with:

| Section | What it sets |
|---|---|
| `link_confidence` | How strongly each link carries a signal to ANT, and to named companies |
| `source_reliability` | Trust in wire services, major outlets, other news, newsletters and exchange filings |
| `half_life_days` | How long each topic keeps counting (momentum 2 days … regulation 30) |
| `grade_scale` | How high/medium/low grades and novelty become numbers |
| `strength_full_at` | The size at which a family's strength reaches 1.0 (macro at a z-score of 4, …) |
| `build` | Used only when AlphaStreet builds the pack; changing it here has no effect |

Edit, then run:

```bash
python3 scripts/apply_weights.py
```

It recomputes `source_reliability`, `half_life_days`, `horizon_days`, `strength`, `confidence`, `materiality`, `novelty` and `link_confidence` across the pack from each signal's `_source.basis`. Commit your `weights.json` with your entry, and say in `ENTRY.md` why you chose it.

## Add your own signals

Put them in [`custom/`](custom/README.md) in the same format and run `python3 scripts/apply_weights.py`. They are merged into the pack by `signal_id`. Any public source is fair: prediction markets, options, analyst notes, filings, your own scraping.

## Known gaps

These are open problems, and room to beat the field:
- **Direction:** `event_direction` is filled only for some families, and for no ANT rows.
- **Exposure:** `exposure` is not measured.
- **Grades:** about two in three news signals have no classifier grade, so their `strength`, `confidence` and `materiality` are `null`.
- **Translation:** Korean, Chinese and Japanese titles are not translated.
- **Themes:** themes that name no company, such as AI capex or GPU shortages, are not in the pack.

The headlines and summaries come from licensed sources. Use them for this hackathon, and do not republish them.
