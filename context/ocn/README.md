# AlphaStreet signals feed (OCN)

AlphaStreet's own market data and news, pulled from OCN, as signals in AlphaStreet's `signal.v1` format. Refreshed daily until submissions close on Wed 18 Nov.

```bash
python scripts/pull_ocn.py        # 1. copy matching OCN rows into raw/ (about 45 minutes for a full scan)
python scripts/build_signals.py   # 2. dedup and structure them into signals/ (seconds)
```

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

v1.1 adds four things to `signal.v1`, all additive (a v1 reader still works):

| Addition | Why |
|---|---|
| `signal_detection`: `signal`, `weak_signal` or `noise` | v1 had nowhere to say a row is noise, so it had to be deleted or mixed in. Noise rows are kept and labelled |
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
| `author`, `author_id`, `engagement`, `parent_id`, `thread_position` | `null` (news has none) |
| `source_reliability` | Starting prior: 0.9 exchange filings, 0.8 wire services and state wires, 0.7 major outlets, 0.6 other news, 0.5 newsletters |
| `scope` | `company` when the story is about one company, `sector` when it names several linked companies |
| `detector`, `subtype` | `news`, and the OCN domain it came from |
| `title`, `title_original`, `language`, `summary` | Headline without OCN's regional prefix ("SK Hynix (000660) [English coverage check]: …"); the same text for non-English titles, which are not translated yet; ISO 639-1 from the script; OCN's summary (at most 600 characters) |
| `region`, `country` | Region from the OCN domain (`TW`, `KR`, `JP`, `CN`, `ME`, else `global`); the country for the four single-country regions |
| `subject_issuer_id`, `ticker` | `ANT` when Anthropic is mentioned, otherwise the one linked company |
| `event_direction`, `strength`, `confidence`, `materiality` | `null` until OCN's classifier grades these rows |
| `evidence` | The rule that set `signal_detection` |
| `novelty` | 1 for the first report in a cluster, 0 for later ones |
| `horizon_days`, `half_life_days` | Twice the half-life, and the longest category prior; with no category, 4 for news, 15 for geopolitical, 25 for newsletters |
| `event_at`, `published_at`, `detected_at`, `valid_until` | `null`, publisher's time, when OCN stored it, `null`. Use `detected_at <= as_of` to stay point-in-time |
| `cluster_id` | One event, one cluster |
| `details` | `{publisher, entities, matched_issuers, matched_in_title}` |
| `producer`, `model`, `prompt_version` | This script; `null`; `null` |
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
