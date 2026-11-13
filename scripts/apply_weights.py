#!/usr/bin/env python3
"""Re-apply signals/weights.json to the signal pack, and merge in your own signals. Standard library only.

  python scripts/apply_weights.py              # rewrites signals/pack/ with the current weights and signals/custom/
  python scripts/apply_weights.py --selftest

Each signal keeps the raw inputs its weighted numbers come from in `_source.basis`, so changing a weight never
needs the original feed. Recomputed here:
  signals:  source_reliability, half_life_days, horizon_days, strength (when it comes from a size measure),
            confidence, materiality, novelty
  impacts:  link_confidence
Your own signals live in signals/custom/*.signals.jsonl and *.impacts.jsonl (see signals/custom/README.md).
They are merged by signal_id, so running this again never duplicates them.
"""
import json, re, sys, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIG = ROOT / "signals"
PACK, CUSTOM = SIG / "pack", SIG / "custom"


def load_weights(path: Path = SIG / "weights.json") -> dict:
    return json.load(open(path))  # a missing key fails below rather than falling back silently


def reliability(s: dict, b: dict, W: dict) -> float:
    r = W["source_reliability"]
    cls = b.get("reliability_class", "news")
    if cls in ("filing", "state_wire", "newsletter"):
        return r[{"filing": "exchange_filing_or_data_release", "state_wire": "state_wire", "newsletter": "newsletter"}[cls]]
    host = urllib.parse.urlsplit(s.get("source_url") or "").netloc.removeprefix("www.")  # the site, not words in the URL path
    hay = f"{s.get('source_name') or ''} {host}".lower()
    return next((v for v, rx in r["patterns"] if re.search(rx, hay)), r["other_news"])


def half_life(s: dict, b: dict, W: dict) -> float:
    hl = W["half_life_days"]
    known = [hl["category"][c] for c in s.get("category") or [] if c in hl["category"]]
    return max(known) if known else hl["no_category"][b.get("half_life_default", "news")]


def strength(b: dict, W: dict) -> float | None:
    if b.get("score") is not None:
        return b["score"]  # a classifier's own score is kept as given
    size = b.get("size")
    if not size or size.get("value") is None:
        return None
    return round(min(1.0, abs(float(size["value"])) / W["strength_full_at"][size["measure"]]), 3)


def weigh_signal(s: dict, W: dict) -> dict:
    b = (s.get("_source") or {}).get("basis")
    if not b:
        return s  # your own signal without a basis: its numbers are used as you wrote them
    g, scale = b.get("grades") or {}, W["grade_scale"]
    hl = half_life(s, b, W)
    s.update({
        "source_reliability": reliability(s, b, W),
        "half_life_days": hl, "horizon_days": hl * 2,
        "strength": strength(b, W),
        "confidence": scale["level"].get(g.get("confidence")),
        "materiality": scale["level"].get(g.get("materiality")),
        "novelty": scale["novelty"][g["novelty"]] if g.get("novelty") in scale["novelty"] else b.get("novelty_from_story"),
    })
    return s


def weigh_impact(i: dict, s: dict | None, W: dict) -> dict:
    if s is None or not (s.get("_source") or {}).get("basis"):
        return i
    lc, b = W["link_confidence"], s["_source"]["basis"]
    if i["issuer_id"] == "ANT" and i["link_type"] == "direct":
        i["link_confidence"] = lc["ant_direct_headline"] if b.get("ant_in_title") else lc["ant_direct_body_only"]
    elif i["issuer_id"] == "ANT" and i["link_type"] == "macro":
        i["link_confidence"] = lc["ant_macro"]
    elif i.get("exposure_source") == "read-through":
        i["link_confidence"] = lc["china_read_through"]
    elif i["link_type"] == "direct":
        i["link_confidence"] = lc["named_company"]
    else:
        i["link_confidence"] = lc[i["link_type"]]
    return i


def apply(sigs: list[dict], imps: list[dict], W: dict) -> None:
    by_id = {s["signal_id"]: weigh_signal(s, W) for s in sigs}
    for i in imps:
        weigh_impact(i, by_id.get(i["signal_id"]), W)


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()] if p.exists() else []


def write_jsonl(p: Path, rows: list[dict]) -> None:
    with open(p, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def merge_custom(sigs: list[dict], imps: list[dict]) -> int:
    """Your signals replace any earlier copy with the same signal_id; their impacts replace that signal's impacts."""
    mine = [dict(s, _source={**(s.get("_source") or {}), "custom": True}) for f in sorted(CUSTOM.glob("*.signals.jsonl")) for s in read_jsonl(f)]
    if not mine:
        return 0
    ids = {s["signal_id"] for s in mine}
    missing = [k for s in mine for k in ("signal_id", "title", "detector", "signal_detection", "published_at") if not s.get(k)]
    if missing:
        sys.exit(f"custom signals are missing required fields: {sorted(set(missing))}")
    sigs[:] = [s for s in sigs if s["signal_id"] not in ids] + mine
    imps[:] = [i for i in imps if i["signal_id"] not in ids] + [i for f in sorted(CUSTOM.glob("*.impacts.jsonl")) for i in read_jsonl(f)]
    return len(mine)


def selftest() -> None:
    W = {"link_confidence": {"supplier": 0.8, "competitor": 0.6, "customer": 0.5, "peer": 0.4, "sector": 0.4, "ant_direct_headline": 1.0,
                             "ant_direct_body_only": 0.5, "named_company": 1.0, "ant_macro": 0.3, "china_read_through": 0.6},
         "source_reliability": {"patterns": [[0.8, "reuters"]], "exchange_filing_or_data_release": 0.9, "state_wire": 0.8, "other_news": 0.6, "newsletter": 0.5},
         "half_life_days": {"category": {"supply": 10, "momentum": 2}, "no_category": {"news": 4, "macro": 4}},
         "grade_scale": {"level": {"high": 0.9, "medium": 0.6}, "novelty": {"step_change": 1.0}},
         "strength_full_at": {"macro_abs_z": 4}}
    s = {"signal_id": "a", "source_name": "Reuters", "category": ["supply", "momentum", "Cloud & Compute Platforms"],
         "_source": {"basis": {"reliability_class": "news", "grades": {"confidence": "high", "novelty": "step_change"}, "ant_in_title": False}}}
    m = {"signal_id": "b", "source_name": "FOMC", "category": [],
         "_source": {"basis": {"reliability_class": "filing", "half_life_default": "macro", "size": {"measure": "macro_abs_z", "value": -3}, "novelty_from_story": 1.0}}}
    imps = [{"signal_id": "a", "issuer_id": "ANT", "link_type": "direct"}, {"signal_id": "a", "issuer_id": "NVDA", "link_type": "direct"},
            {"signal_id": "a", "issuer_id": "ANT", "link_type": "supplier"}, {"signal_id": "b", "issuer_id": "ANT", "link_type": "macro"},
            {"signal_id": "a", "issuer_id": "AMD", "link_type": "competitor", "exposure_source": "read-through"}]
    apply([s, m], imps, W)
    assert (s["source_reliability"], s["half_life_days"], s["horizon_days"], s["confidence"], s["novelty"]) == (0.8, 10, 20, 0.9, 1.0)
    assert (m["source_reliability"], m["half_life_days"], m["strength"], m["novelty"]) == (0.9, 4, 0.75, 1.0)
    assert [i["link_confidence"] for i in imps] == [0.5, 1.0, 0.8, 0.3, 0.6]
    W["link_confidence"]["supplier"] = 0.95  # a changed weight flows straight through
    apply([s, m], imps, W)
    assert imps[2]["link_confidence"] == 0.95
    own = {"signal_id": "mine", "strength": 0.4, "_source": {"custom": True}}
    assert weigh_signal(dict(own), W)["strength"] == 0.4  # no basis: left as written
    print("selftest ok")


def main() -> int:
    if "--selftest" in sys.argv:
        selftest()
        return 0
    sp, ip = PACK / "signals.v1.jsonl", PACK / "signal_impacts.v1.jsonl"
    if not sp.exists():
        sys.exit("signals/pack/signals.v1.jsonl is missing. The pack is published with the repo; see signals/README.md.")
    sigs, imps = read_jsonl(sp), read_jsonl(ip)
    added = merge_custom(sigs, imps)
    apply(sigs, imps, load_weights())
    sigs.sort(key=lambda s: s.get("published_at") or s.get("detected_at") or "", reverse=True)
    write_jsonl(sp, sigs)
    write_jsonl(ip, imps)
    print(f"weighted {len(sigs)} signals and {len(imps)} impacts; {added} of your own signals merged from signals/custom/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
