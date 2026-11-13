# AlphaStreet signals feed (OCN)

AlphaStreet's own market data and news, pulled from OCN, as signals in AlphaStreet's `signal.v1` format. Refreshed daily until submissions close on Wed 18 Nov.

```bash
python scripts/pull_ocn.py        # 1. copy matching OCN rows into raw/ (about 45 minutes for a full scan)
python scripts/build_signals.py   # 2. dedup and structure them into signals/ (seconds)
```

## Weights

Every tunable number is in [`weights.json`](weights.json), and the numbers quoted below are its current values:
- link confidence for each link to ANT
- source reliability priors
- half-lives by category
- how OCN's high/medium/low grades become numbers
- the size at which each family's strength reaches 1.0
- dedup windows and thresholds
- which macro channels and geopolitical categories count as relevant

Edit it and rerun `python3 scripts/build_signals.py`; no new pull is needed. The `relevance` block is the exception: it is applied when pulling, so rerun `python3 scripts/pull_ocn.py`. A missing key stops the build instead of falling back to a hidden default. The 48 linked companies are in [`../markets/links.csv`](../markets/links.csv). Noise rules and topic keywords stay in `build_signals.py`; bump `RULES_VERSION` there when you change them.

## Browse it

Two pages in `explorer/` read the pack straight from `signals/`, so they hold no data of their own:

```bash
python3 -m http.server 8000      # from the repo root
```

- `http://localhost:8000/explorer/signals.html`: every signal, filterable by detector, label, link to ANT, scope, source kind, category and subject; a row opens every field and its impact rows.
- `http://localhost:8000/explorer/impacts.html`: every signal-to-company impact, with the most-affected companies, filters by link, direction, detector, hops, confidence and exposure source, and private companies marked.

## What is pulled

OCN does not track Anthropic as a company yet, so rows are found by text. An article is kept when either:

- it mentions Anthropic anywhere: Anthropic, Amodei, Claude product names. "Claude" alone is left out because it is also a first name.
- its headline names a company linked to Anthropic in [`../markets/links.csv`](../markets/links.csv). Alpha Vantage's ticker tags on company_news are not enough on their own: they mark every company an article mentions, so they pulled in ETF holdings lists and dividend notices.

| Link to Anthropic | Companies | What it tells you |
|---|---|---|
| `competitor` | OpenAI, xAI (SpaceX), Meta, Mistral, DeepSeek, Cohere, Alibaba (Qwen), Moonshot (Kimi), Perplexity | Rival launches, pricing and funding |
| `supplier` | Amazon, Google, Microsoft, Nvidia, AMD, Broadcom, Micron, TSMC, SK Hynix, Samsung, ASML, TeraWulf, Hut 8, Equinix, Digital Realty, Constellation Energy, Vistra | Supply: compute, chips, memory, data centres and power. Several are also investors |
| `customer` | Palantir, ServiceNow, Salesforce, Snowflake, Datadog, Cloudflare, Accenture, Deloitte, Cursor (Anysphere), GitLab, Notion, Zoom | Demand: enterprise AI spending |
| `peer` | CoreWeave, Oracle, Nebius, Super Micro, Dell | The AI-capex trade ANT will trade with |
| `sector` | Akamai, Arista, Vertiv, Arm, Marvell | AI infrastructure |

Samsung counts only when the headline is also about chips, memory or AI, so phone news stays out. Ambiguous names (Cursor, Notion, Zoom, Arm) need a qualifier next to them.

## OCN's graded signals (regional, geopolitical, macro)

With `OCN_API_KEY` set, `pull_ocn.py` also pulls OCN's own graded signal families into `raw/signals/<family>.jsonl.gz`, one event per row. Only rows that can move the Anthropic IPO are kept, and only rows OCN labelled `signal` or `weak_signal`. A `weak_signal` must name Anthropic or a linked company in its headline (geopolitical: or have a chip, export-control or AI headline); a full `signal` may also qualify through OCN's tickers, read-through, categories and assets:

| Family | Detector | Kept when |
|---|---|---|
| Taiwan (MOPS filings, revenue, press) | `taiwan` | It names Anthropic or a linked company, by name, ticker or local code (TSMC 2330) |
| Korea (DART filings, exports, press) | `korea` | Same; SK Hynix 000660, Samsung 005930 |
| Japan (TDnet filings, press) | `japan` | Same |
| China (CNINFO/HKEX filings, policy, press) | `china` | Same, including the US tickers in OCN's read-through (Alibaba 09988) |
| Middle East | `middle_east` | Same, including the affected companies OCN lists |
| Geopolitical | `geo` | It names a linked company, has a chip, export-control or AI headline, or (full signals) falls in OCN's chip, compute, cloud, networking, AI-capital or sovereign-AI categories or touches semiconductor or equity assets. Fuel, grid and local data-centre permitting stories are left out |
| Macro (FRED and agency releases) | `macro` | It moves the discount rate or risk appetite: the policy-path, discount-rate, credit, liquidity and risk-appetite channels, and level extremes in rate, credit and volatility series. Energy, housing and consumer data are left out. Suppressed audit rows are left out |

Each family maps into the same `signal.v1.1` row. OCN's own grades fill what the article rules cannot:

| v1.1 field | From OCN |
|---|---|
| `signal_detection`, `evidence` | OCN's label and reason |
| `strength`, `confidence`, `materiality`, `novelty` | `signal_score` when OCN has one; otherwise strength from the family's own size measure, capped at 1: macro \|z\|/4 (or distance from the median percentile for level extremes), China deviation in MADs/10 or capex change/100%, Taiwan revenue deviation/3, Korea % of revenue/30, Japan profit revision/50%. Confidence and materiality high/medium/low as 0.9/0.6/0.3 where OCN has them. Novelty: the replaced article's place in its story, else 1 (one row per OCN event) |
| `event_direction` | Taiwan: sign of the revenue deviation. Japan: sign of the profit or revenue revision. Middle East: good +1, bad −1, mixed 0. Others: `null` |
| `event_at` | The filing's fact date, the press event date, the Middle East event date, the geopolitical event date, or the macro observation date |
| `published_at`, `detected_at` | OCN's `published` (for macro, when the number was released) and `stored_at` |
| `title`, `title_original` | OCN's English translation, and the original |
| `details` | Typed per detector, as in the v1 spec: taiwan `local_code, source_category, revenue_deviation_pct, clause_label`; korea `stock_code, report_type`; japan `local_code, source_category, typical_direction`; china `local_code, signal_type, link_direction`; middle_east `affected_companies, effect`; geo `channel, assets`; macro `series_id, channel, transmission, z_score, move_bp` |

Impacts: ANT gets a `direct` row when Anthropic is named; otherwise one row per link type of each named company. Macro, geopolitical and Middle East signals also reach ANT as `macro` at confidence 0.3, a flat prior until ANT has a price history to measure. Named companies get `direct` rows, with direction from OCN where it has one (Middle East share-price effects, China read-through relationships). When OCN graded an article that the article pull also kept, the graded row replaces it.

## Dedup (the same rules OCN uses)

1. **URL.** One row per canonical URL. The same story filed under two OCN domains becomes one signal, listing both domains.
2. **Near-duplicate headlines**, per OCN domain, inside OCN's time window and similarity threshold for that domain:

   | Domain | Window | Threshold |
   |---|---|---|
   | ai_news, smart_money | 48 h | 0.90 |
   | geopolitical_news, middle_east_market_signal | 168 h | 0.82 |
   | korea_market_signal | 24 h | 0.90 |
   | japan_market_signal | 24 h, same Tokyo day | 0.95 |

   Similarity is the cosine of OCN's stored title embeddings. If a pair lacks embeddings, the titles must be at least 90% the same text. The dropped copy is kept on the surviving signal as `also_reported_by: {outlet, title, url}`, so you can see how each outlet worded it. Filing and official-wire rows are never merged, as in OCN.
3. **Clusters.** Signals from different domains that cover the same story share a `cluster_id`, so one event counts once. The first to report it gets `novelty` 1; later ones get 0.

## Files

| File | What it is |
|---|---|
| `signals/signals.v1.jsonl` | One row per signal (the event, once), newest first |
| `signals/signal_impacts.v1.jsonl` | One row per signal × affected company. ANT always has a row: `direct` if the story is about Anthropic, otherwise through its link (`competitor`, `supplier`, ...) |
| `signals/stats.json` | Counts at each dedup step |
| `signal.v1.schema.json` | The contract for both files. Every field is present; a field this pack cannot fill yet is `null` |
| `raw/` | The OCN rows exactly as pulled (internal) |

## `signal.v1.1`

v1.1 drops the five social-post fields (`author`, `author_id`, `engagement`, `parent_id`, `thread_position`) until the pack has social sources, and adds four things to `signal.v1`:

| Addition | Why |
|---|---|
| `signal_detection`: `signal`, `weak_signal` or `noise` | v1 had nowhere to record relevance. This pack holds only data that can move the Anthropic IPO, so `noise` rows are left out and counted in `stats.json`; the label separates strong from weak |
| `category`: a list of topics | The plan sets half-life priors by category, but v1 had no category field. `half_life_days` is now the longest prior among a signal's categories |
| `details.matched_in_title` | Which companies the headline names, as against the body only. A body-only mention is weaker |
| Impact key `(signal_id, issuer_id, link_type)` | One company can be linked more than one way: a Google story reaches ANT as both `supplier` and `competitor` |

How the labels are set here (headline rules, until OCN's classifier grades these rows; the rule is named in `evidence`):

- `noise`: share-holding notices, Form 4 and insider-trade notices, quote and balance-sheet pages, templated price moves ("If you invested…", "stock falls 1.1%", unless the cause is in the headline), law-firm ads, dividend and ETF notices, earnings-call scheduling. Never when the headline names Anthropic.
- `weak_signal`: Anthropic named only in the body of a story whose headline names no linked company, or only peer and sector companies named.
- `signal`: everything else.
- `category`: keyword rules over the headline and summary: ipo, funding, earnings, pricing, product, demand, supply, competition, talent, legal, regulation, export_controls, geopolitics, momentum. Half-life priors: 2 days for momentum, pricing, competition, demand; 4 for earnings, product, talent; 10 for ipo, funding, supply, geopolitics; 30 for legal, regulation, export controls.

**`signals.v1.jsonl`**: the event, once.

| Field | In this pack |
|---|---|
| `signal_id`, `schema_version` | `ocn:<article id>`, `1` |
| `source_kind` | `news_article`; `long_form` for Substack and newsletters; `exchange_disclosure` for Korean DART filings and quarterly results, HKEX and TSE disclosures |
| `source_name`, `source_url` | The outlet and the article link |
| `source_reliability` | Starting prior: 0.9 exchange filings, 0.8 wire services and state wires, 0.7 major outlets, 0.6 other news, 0.5 newsletters |
| `scope` | `company` when the story is about one company, `sector` when it names several linked companies |
| `detector`, `subtype` | `news`, and the OCN domain it came from |
| `title`, `title_original`, `language`, `summary` | Headline without OCN's regional prefix ("SK Hynix (000660) [English coverage check]: …"); the same text for non-English titles, which are not translated yet; ISO 639-1 from the script; OCN's summary (at most 600 characters) |
| `region`, `country` | Region from the OCN domain (`TW`, `KR`, `JP`, `CN`, `ME`, else `global`); the country for the four single-country regions |
| `subject_issuer_id`, `ticker` | `ANT` when Anthropic is mentioned, otherwise the one linked company |
| `strength`, `confidence`, `materiality` | From OCN's AI-news classifier where it graded the article (score; high/medium/low as 0.9/0.6/0.3). Its `noise` grade removes the article from the pack, unless Anthropic is in the headline (then `weak_signal`). Otherwise `null` |
| `event_direction` | `null` until a classifier judges good or bad for the subject |
| `evidence` | The rule that set `signal_detection` |
| `novelty` | OCN's novelty where it graded the article (step_change 1, meaningful 0.66, incremental 0.33, repeated 0); otherwise 1 for the first report in a cluster, 0 for later ones |
| `horizon_days`, `half_life_days` | Twice the half-life, and the longest category prior; with no category, 4 for news, 15 for geopolitical, 25 for newsletters |
| `event_at`, `published_at`, `detected_at`, `valid_until` | `null`, publisher's time, when OCN stored it, `null`. Use `detected_at <= as_of` to stay point-in-time |
| `cluster_id` | One event, one cluster |
| `details` | `{publisher, entities, matched_issuers, matched_in_title}` |
| `producer`, `model`, `prompt_version` | `ocn/signal-detection-agent` when OCN graded the article, with `model`/`prompt_version` `null` (OCN does not record them yet); otherwise this script, `rules` and the rule-set version |
| `signal_type`, `superseded_by`, `superseded_at` | `state` for quarterly results, each superseded by the next quarter's; `event` for the rest |
| `backfilled` | `true` when OCN found the row more than 7 days after it was published. `detected_at` stays OCN's real time, so point-in-time filtering is still correct |
| `_ocn` | Provenance, not part of the contract: OCN article id, domains, matched words, `also_reported_by`, Alpha Vantage sentiment |

**`signal_impacts.v1.jsonl`**: one row per signal × company.

| Field | In this pack |
|---|---|
| `signal_id`, `issuer_id`, `link_type` | Together, the key |
| `link_type`, `link_hops` | `direct` and 0 for a company the headline names, and for ANT when the story mentions Anthropic. When the headline names linked companies but not Anthropic, ANT gets one row per link type, at 1 hop |
| `direction` | -1, 0 or +1 from Alpha Vantage's per-ticker sentiment where it exists (company_news), else `null` |
| `exposure`, `exposure_source` | `null`, `not measured` |
| `link_confidence` | 1.0 for named companies and for Anthropic in the headline, 0.5 for Anthropic in the body only. For ANT through a link: supplier 0.8, competitor 0.6, customer 0.5, peer or sector 0.4 |

## Not yet

- Grading (`event_direction`, `strength`, `confidence`, `materiality`): needs OCN's classifier run on these rows, or `OCN_API_KEY` to pull its existing grades for the rows it already graded.
- Embeddings for the titles that lack one, so every pair is compared the way OCN compares them.
- Themes with no company named (AI capex, GPU shortages) are not pulled.
- Translation: Korean, Chinese and Japanese titles are kept as they are, in both `title` and `title_original`.
- `signal_detection` and `category` are headline rules. OCN's classifier should replace them; until then about 1 in 5 signals has no category.

Google is linked as both `supplier` and `competitor`; Amazon and Microsoft as both `supplier` and `customer` (they resell Claude). Hana Micron is not Micron.

`raw/` and `signals/` are in `.gitignore`. The headlines and summaries come from licensed sources, and this repo goes public, so the pack is committed only once it is cleared for publishing. How the signals are produced is not part of the hackathon.
