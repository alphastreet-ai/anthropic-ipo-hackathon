#!/usr/bin/env python3
"""Example 3: no LLM, no API key, standard library only. A base-rate model you can beat.

  python examples/baseline/run.py --out submissions/<your-handle>/ --name <your-handle>/baseline

IPO price = the midpoint of the filed range. Day one and day five = that price moved by the
first-day returns of past large IPOs (context/comps/). Your entry does not have to look like this,
or be Python: anything that writes a valid prediction.json counts.
"""
import argparse, csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True), ap.add_argument("--name", default="you/baseline")
ap.add_argument("--repo", default="https://github.com/alphastreet-ai/anthropic-ipo-hackathon", help="link to your code")
args = ap.parse_args()

offering = {r["item"]: r["value"] for r in csv.DictReader(open(ROOT / "context/anthropic-s1/offering.csv"))}
low, high = float(offering["price_range_low"]), float(offering["price_range_high"])
comps = list(csv.DictReader(open(ROOT / "context/comps/large_ipos.csv")))
open_pop = sum(float(c["day1_open_usd"]) / float(c["ipo_price_usd"]) - 1 for c in comps) / len(comps)
# ponytail: averages one comp with the long-run large-cap figure (10%, context/comps/base_rates.md); fit a real model once large_ipos.csv has more rows
close_pop = (sum(float(c["day1_return_pct"]) for c in comps) / len(comps) / 100 + 0.10) / 2


def call(median: float, spread: float) -> dict:
    return {"median": round(median, 2), "p10": round(median * (1 - spread), 2), "p90": round(median * (1 + spread), 2)}


ipo = (low + high) / 2
prediction = {
    "name": args.name, "method": "Base rates from past large IPOs, plain Python, no LLM", "repo_url": args.repo,
    "ipo_price": {"median": ipo, "p10": low, "p90": high + 2},
    "day1_open": call(ipo * (1 + open_pop), 0.20),
    "day1_close": call(ipo * (1 + close_pop), 0.25),
    "day5_close": call(ipo * (1 + close_pop), 0.30),  # no fade data yet, so day five = day one with a wider range
    "thesis": f"A base-rate view with no opinion on Anthropic itself. Deals price near the middle of the filed ${low:g} to ${high:g} range more often than at either end. "
              f"Past large IPOs opened about {open_pop:.0%} above the IPO price and closed the first day about {close_pop:.0%} above it. Day five is assumed equal to day one, with a wider range.",
    "key_risks": ["The range is raised or the deal prices above it, as hot deals often do", "One comparable IPO is a thin base for the day-one move",
                  "A sharp move in AI stocks after Nvidia's results on 17 Nov changes demand overnight"],
    "confidence": 0.3,
}
out = Path(args.out) / "prediction.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(prediction, indent=2) + "\n")
print(f"wrote {out}\ncheck it: python scripts/validate.py {out}")
