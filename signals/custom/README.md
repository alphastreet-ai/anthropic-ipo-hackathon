# Your own signals

Add any signal you think moves the Anthropic IPO, from any public source, and it joins the pack:

1. Write your signals to `signals/custom/<name>.signals.jsonl`, one JSON object per line, in the `signal.v1.1` shape ([`../signal.v1.schema.json`](../signal.v1.schema.json)).
2. Write how each one reaches companies to `signals/custom/<name>.impacts.jsonl`, one row per signal and company.
3. Run `python3 scripts/apply_weights.py`. Your rows are merged into `signals/pack/` by `signal_id`, so running it again replaces them instead of adding copies.

Start from [`example.signals.jsonl.sample`](example.signals.jsonl.sample) and [`example.impacts.jsonl.sample`](example.impacts.jsonl.sample). Files ending in `.sample` are not merged.

**Required on every signal:** `signal_id`, `title`, `detector`, `signal_detection` and `published_at`.

**Recommended:**
- Give `signal_id` your own prefix, such as `you:` followed by a number, so it cannot clash with the pack's ids.
- Set `detector` to `custom`, or to the closest existing detector.
- Fill `source_url`, so judges can check where the signal came from.

**Weighted numbers:** your numbers (`strength`, `confidence`, `source_reliability`, `half_life_days`, `link_confidence`) are used as you write them, because your rows carry no `_source.basis`. Add a `basis` if you want `weights.json` to set them instead; see how the pack's own rows do it.

**Point-in-time:** keep `detected_at` honest, meaning the time you could first have known it. Use `published_at` if you are unsure. A signal you could only know after the IPO price was set is no use for predicting it.
