#!/usr/bin/env python3
"""ANT IPO entry: perp-anchored, oversubscription-adjusted, comp-faded. Stdlib only.
  python submissions/Pritam991/run.py --name Pritam991/perp-anchor --repo https://github.com/Pritam991/anthropic-ipo-hackathon
Every constant below is an input with its source; change it and rerun.
"""
import argparse, csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--name", default="Pritam991/perp-anchor"); ap.add_argument("--repo", default="https://github.com/Pritam991/anthropic-ipo-hackathon")
a = ap.parse_args()

off = {r["item"]: r["value"] for r in csv.DictReader(open(ROOT / "context/anthropic-s1/offering.csv"))}
lo, hi = float(off["price_range_low"]), float(off["price_range_high"])

# --- evidence (all dated, see ENTRY.md) ---
PERP = [("2026-11-10", 98.0, "CNBC, Hyperliquid"), ("2026-11-12", 103.93, "Crypto Briefing, perp close")]
OVERSUB = 6.0            # Bloomberg 12-13 Nov
P_ABOVE_RANGE = 0.55     # FT 11 Nov: bankers weigh pricing above range; judgement
PERP_SHRINK = 0.60       # weight on perp vs comp-based pop; perps ran +30% over range, SpaceX perp converged to equity at IPO
COMP_OPEN, COMP_CLOSE = 0.11, 0.19   # SpaceX 150/135, 160.95/135 (context/comps)

perp = sum(p for _, p, _ in PERP) / len(PERP)
# IPO price: mid of range, pulled up by demand. Mega-deal size caps how far banks lift it.
ipo = hi + (P_ABOVE_RANGE * 0.04 * hi)       # ~ $79.7 median when above-range is likely
ipo = round(ipo, 2)
# Day-1 anchor: blend perp with comp pop on the IPO price.
comp_open, comp_close = ipo * (1 + COMP_OPEN), ipo * (1 + COMP_CLOSE)
open_ = PERP_SHRINK * perp + (1 - PERP_SHRINK) * comp_open
close = PERP_SHRINK * perp * 1.02 + (1 - PERP_SHRINK) * comp_close   # slight extra drift: retail 20% + passive demand
# Day 5: SpaceX ran +40% by day 3 then round-tripped; fat tails, median fades ~4% from day-1 close.
day5 = close * 0.96

def call(m, lo_f, hi_f): return {"median": round(m, 2), "p10": round(m * lo_f, 2), "p90": round(m * hi_f, 2)}
pred = {
    "name": a.name, "method": "Perp-anchored blend with SpaceX comp pop; stdlib Python, no LLM", "repo_url": a.repo,
    "ipo_price": {"median": ipo, "p10": 73.0, "p90": 90.0},
    "day1_open": call(open_, 0.80, 1.27), "day1_close": call(close, 0.76, 1.33), "day5_close": call(day5, 0.68, 1.45),
    "thesis": (f"Demand is the story: ~{OVERSUB:.0f}x oversubscribed (Bloomberg), a $115B+ book and FT reports that bankers weigh pricing above the ${lo:g}-${hi:g} range, so I put the IPO price just above the top ({ipo}). "
               f"The pre-IPO perp is the best market-implied price: ${PERP[0][1]} on 10 Nov and ${PERP[1][1]} on 12 Nov, 30%+ above the range. SpaceX's perp converged to the equity price at its IPO, so I weight the perp 60% and the SpaceX pop (+11% open, +19% close) 40%. "
               "Only 6.3% of shares float, with 20% retail and ~$19B passive demand, which supports day one. Day five is faded 4% and given a wide, right-skewed range: SpaceX rose ~40% in three days, peaked at +56%, then later fell below its IPO price (about -50% from the peak by late July), and ANT carries a 316M-share lockup release and a $901B commitment load."),
    "key_risks": ["Nvidia reports 17 Nov, the day before pricing, after CEO Jensen Huang's resignation; a bad print hits placement partner demand.",
                  "The perp trades thin and offshore ($250M a day) and has swung $94-$138; it may overstate the equity open.",
                  "Pricing at the top of the range, not above it, would cut my IPO-price and open estimates.",
                  "A SpaceX-style fade: day five could land below day one if the AI trade turns, since only one mega comp is used.",
                  "Valuation skeptics (Ackman) and rising Treasury yields could cap the pop for a ~$1T+ company."],
    "confidence": 0.35,
}
out = Path(__file__).resolve().parent / "prediction.json"
out.write_text(json.dumps(pred, indent=2) + "\n"); print(json.dumps({k: v for k, v in pred.items() if k in ("ipo_price","day1_open","day1_close","day5_close")}, indent=1))

