#!/usr/bin/env python3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model

DAYS_BEFORE = 4


def replay(ticker, inputs, comp_rows, calibration_rows):
    comp = next(row for row in comp_rows if row["ticker"] == ticker)
    path = next(c for c in inputs["comps"] if c["ticker"] == ticker)
    perp = {int(row["lag_days"]): row for row in calibration_rows if row["ticker"] == ticker}
    listed = date.fromisoformat(comp["first_trade_date"])
    deal = {"expected_first_trade_date": listed.isoformat(), "expected_pricing_date": (listed - timedelta(days=1)).isoformat(),
            "max_price_over_range_top_without_new_filing": 1.2}
    if comp["first_range_high"]:
        deal["first_range"] = deal["last_range"] = [float(comp["first_range_low"]), float(comp["first_range_high"])]
    else:
        deal["fixed_price"] = float(comp["ipo_price"])
    then = {
        "deal": deal,
        "comps": [c for c in inputs["comps"] if c["listed"] < listed],
        "precedents": [row for row in inputs["precedents"] if row["listed"] < listed],
        "perp_closes": [float(perp[lag]["perp_close"]) for lag in sorted(perp, reverse=True) if lag >= DAYS_BEFORE],
        "perp_mark": float(perp[DAYS_BEFORE]["perp_close"]),
        "prediction_market": None,
        "as_of": listed - timedelta(days=DAYS_BEFORE),
    }
    ipo = path["ipo"]
    actual = {"ipo_price": ipo, "day1_open": path["open"] * ipo, "day1_close": path["close1"] * ipo, "day5_close": path["close5"] * ipo}
    return model.forecast(then)["calls"], actual, sorted({row["ticker"] for row in then["precedents"]})


def main():
    inputs = model.load_inputs()
    comp_rows = model.read_csv(model.DATA / "comps.csv")
    calibration_rows = model.read_csv(model.DATA / "perp_calibration.csv")
    listings = sorted({(row["first_trade_date"], row["ticker"]) for row in calibration_rows})
    print("listing  target         p10   median      p90    actual  inside   score")
    scores = []
    for listed, ticker in listings:
        calls, actual, known = replay(ticker, inputs, comp_rows, calibration_rows)
        print(f"{ticker} listed {listed}; earlier pre-IPO perpetuals known then: {', '.join(known) or 'none'}")
        for target in model.TARGETS:
            call, real = calls[target], actual[target]
            if target == "ipo_price" and call["p10"] == call["p90"]:
                continue  # a fixed-price deal has no IPO price to forecast
            score = model.price_score(call, real)
            scores.append(score)
            inside = "yes" if call["p10"] <= real <= call["p90"] else "no"
            print(f"         {target:<11} {call['p10']:>7.2f} {call['median']:>8.2f} {call['p90']:>8.2f} {real:>9.2f}  {inside:<6} {score:>6.3f}")
    print(f"mean score {sum(scores) / len(scores):.3f} over {len(scores)} calls (the hackathon gives 50 points times this)")


if __name__ == "__main__":
    main()
