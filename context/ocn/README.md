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

## `signal.v1`

**`signals.v1.jsonl`**: the event, once.

| Field | In this pack |
|---|---|
| `signal_id`, `schema_version` | `ocn:<article id>`, `1` |
| `source_kind` | `news_article`, or `long_form` for Substack and newsletters |
| `source_name`, `source_url` | The outlet and the article link |
| `author`, `author_id`, `engagement`, `parent_id`, `thread_position` | `null` (news has none) |
| `source_reliability` | Starting prior: 0.8 wire services, 0.7 major outlets, 0.6 other news, 0.5 newsletters |
| `scope` | `company` when the story is about one company, `sector` when it names several linked companies |
| `detector`, `subtype` | `news`, and the OCN domain it came from |
| `title`, `title_original`, `language`, `summary` | Headline, `null`, ISO 639-1, OCN's summary (at most 600 characters) |
| `region`, `country` | Region from the OCN domain (`TW`, `KR`, `JP`, `CN`, `ME`, else `global`); `null` |
| `subject_issuer_id`, `ticker` | `ANT` when Anthropic is mentioned, otherwise the one linked company |
| `event_direction`, `strength`, `confidence`, `materiality`, `evidence` | `null` until OCN's classifier grades these rows |
| `novelty` | 1 for the first report in a cluster, 0 for later ones |
| `horizon_days`, `half_life_days` | Priors: news 5 and 4, geopolitical 30 and 15, newsletters 50 and 25 |
| `event_at`, `published_at`, `detected_at`, `valid_until` | `null`, publisher's time, when OCN stored it, `null`. Use `detected_at <= as_of` to stay point-in-time |
| `cluster_id` | One event, one cluster |
| `details` | `{publisher, entities, matched_issuers}` |
| `producer`, `model`, `prompt_version` | This script; `null`; `null` |
| `signal_type`, `superseded_by`, `superseded_at`, `backfilled` | `event`, `null`, `null`, `false` |
| `_ocn` | Provenance, not part of the contract: OCN article id, domains, matched words, `also_reported_by`, Alpha Vantage sentiment |

**`signal_impacts.v1.jsonl`**: one row per signal × company.

| Field | In this pack |
|---|---|
| `signal_id`, `issuer_id` | Together, the key |
| `link_type`, `link_hops` | `direct` and 0 for a company the story names; for ANT on a story about a linked company, the link type and 1 |
| `direction` | -1, 0 or +1 from Alpha Vantage's per-ticker sentiment where it exists (company_news), else `null` |
| `exposure`, `exposure_source` | `null`, `not measured` |
| `link_confidence` | 1.0 for named companies. For ANT through a link: supplier 0.8, competitor 0.6, customer 0.5, peer or sector 0.4 |

## Not yet

- Grading (`event_direction`, `strength`, `confidence`, `materiality`): needs OCN's classifier run on these rows, or `OCN_API_KEY` to pull its existing grades for the rows it already graded.
- Embeddings for the titles that lack one, so every pair is compared the way OCN compares them.
- Themes with no company named (AI capex, GPU shortages) are not pulled.

`raw/` and `signals/` are in `.gitignore`. The headlines and summaries come from licensed sources, and this repo goes public, so the pack is committed only once it is cleared for publishing. How the signals are produced is not part of the hackathon.
