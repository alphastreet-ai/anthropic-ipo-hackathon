#!/usr/bin/env python3
"""Produce prediction.json for the Anthropic IPO."""
import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_data
import model

ROOT = HERE.parents[1]
METHOD = "Pre-IPO perpetual checked against earlier listings, prediction-market odds and IPO-history kernel model, drawn jointly; Python standard library"


def usd(value):
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def millions(shares):
    return f"{shares / 1e6:,.0f} million"


def price_cap(deal):
    return math.floor(deal["max_price_over_range_top_without_new_filing"] * deal["last_range"][1])


def write_thesis(result, inputs):
    calls, notes, deal = result["calls"], result["diagnostics"], inputs["deal"]
    clocks = {clock["name"]: clock for clock in notes["clocks"]}
    low, high = deal["last_range"]
    odds, perp, history = notes["ipo_price_odds"], clocks["perp"], clocks["history"]
    near = [f"{ticker} ({capture:.2f})" for ticker, capture in perp["capture_by_listing"].items() if capture >= 0.5]
    far = [f"{ticker} ({capture:.2f})" for ticker, capture in perp["capture_by_listing"].items() if capture < 0.5]
    parts = [
        f"Anthropic filed a {usd(low)} to {usd(high)} range for {millions(deal['shares_offered'])} shares ({deal['filing_label']}). "
        f"Three clocks, weighted equally, say where the stock closes on day one. "
        f"(1) The pre-IPO perpetual marks {usd(perp['mark'])}. Earlier listings with such a perpetual show how much of the gain it expected over the IPO price "
        f"had arrived by the first close: {', '.join(near)} landed near it, {', '.join(far) or 'none'} closed at the IPO price. "
        f"The model lets ANT land between its IPO price and the perpetual in those proportions (median {usd(perp['median_usd'])})."
    ]
    if "market" in clocks:
        parts.append(f"(2) Prediction-market odds on the first-day closing value centre on {usd(clocks['market']['median_usd'])}.")
    parts.append(
        f"(3) The {len(history['deals'])} largest past IPOs gained a median {history['median_gain']:.0%} on day one, though {history['flat_or_down']} of them gained under 5%; "
        f"applied to the IPO price that gives {usd(history['median_usd'])}. Together the clocks put the first close at {usd(calls['day1_close']['median'])}, "
        f"with a {notes['chance_first_day_gain_under_5pct']:.0%} chance that the first-day gain is under 5%."
    )
    parts.append(
        f"Banks do not price to the expected trading level. Across {notes['comps_used']} past IPOs the offer price moved only part of the way from the filed range "
        f"toward where the stock then traded, so the model calls the IPO price at {usd(calls['ipo_price']['median'])}: {odds['above_range']:.0%} above the range, "
        f"{odds['at_top']:.0%} exactly at the top, {odds['inside_range_below_top'] + odds['below_range']:.0%} lower. "
        f"The book is reported about {deal['reported_book_coverage_x']} times covered and press reports point to a price above {usd(deal['reported_price_above'])}; "
        f"without a new range filing, SEC Rule 430A caps the price near {usd(price_cap(deal))}."
    )
    parts.append(
        f"The first trade is called at {usd(calls['day1_open']['median'])}, because IPOs have opened slightly above their first close on average. "
        f"Day five is centred {notes['median_day5_vs_day1']:+.0%} from day one with a wide range: SpaceX, the only deal of this size, rose 15% from its first close "
        f"to its fifth and fell 16% the next session. Two terms of this deal make that week harder to call: up to {millions(deal['employee_shares_sellable_from_day_one'])} "
        f"employee shares may be sold from the first trading day, and up to {deal['retail_allocation_up_to_pct']}% of the offering goes to retail accounts."
    )
    return " ".join(parts)


def write_risks(inputs):
    deal = inputs["deal"]
    return [
        "Nvidia beat forecasts on 17 Nov, the evening before pricing and days after its CEO resigned, and its shares rose after hours. "
        "Nvidia is also buying $10 billion of the concurrent placement, so a reversal on pricing day pulls the perpetual, the IPO price and day one down together.",
        "The pre-IPO perpetual can be froth: Quantinuum's sat near $95 before a stock that closed day one at its $60 IPO price. "
        "The model gives that outcome one chance in three inside the perpetual clock; if it is likelier, the trading calls are too high.",
        f"A formal range increase before pricing, as Facebook, Alibaba and Rivian did, would lift the IPO price above this call. "
        f"Without one the price cannot exceed about {usd(price_cap(deal))}.",
        f"Up to {millions(deal['employee_shares_sellable_from_day_one'])} employee shares are free to sell from the first trading day, "
        f"{deal['employee_shares_sellable_from_day_one'] / deal['shares_offered']:.0%} on top of the {millions(deal['shares_offered'])} offered, "
        f"so first-week supply is unusually elastic.",
        "Day five is 25 Nov, the session before Thanksgiving. SpaceX gained 15% between its first and fifth close and gave it all back a day later, "
        "so the fifth close can sit far from fair value in either direction.",
    ]


def write_signals(result, inputs, out):
    notes, deal = result["diagnostics"], inputs["deal"]
    clocks = {clock["name"]: clock for clock in notes["clocks"]}
    trust = json.loads((ROOT / "signals" / "weights.json").read_text())["source_reliability"]
    high = deal["last_range"][1]
    stamp = notes["as_of"] + "T00:00:00+00:00"
    listing = deal["expected_first_trade_date"] + "T21:00:00+00:00"

    def signal(key, **fields):
        base = {"signal_id": f"neelabhra08:{key}", "schema_version": 1, "scope": "company", "detector": "custom", "signal_detection": "signal",
                "category": ["ipo"], "title_original": None, "language": "en", "region": "US", "country": "US", "subject_issuer_id": "ANT",
                "ticker": "ANT", "novelty": None, "horizon_days": 4, "half_life_days": 2, "event_at": stamp, "published_at": stamp,
                "detected_at": stamp, "valid_until": listing, "cluster_id": f"neelabhra08:{key}", "producer": "neelabhra08/three-clocks",
                "model": None, "prompt_version": None, "signal_type": "state", "superseded_by": None, "superseded_at": None, "backfilled": False}
        # The schema fixes the keys of `details`, so the numbers behind each signal travel in `_source`.
        details = {"publisher": fields["source_name"], "entities": None, "matched_issuers": ["ANT"], "matched_in_title": ["ANT"]}
        reading = fields.pop("reading")
        return {**base, **fields, "details": details, "_source": {"reading": reading}}

    perp = clocks["perp"]
    premium = perp["mark"] / high - 1
    captures = perp["capture_by_listing"]
    held_up = sum(1 for capture in captures.values() if capture >= 0.5) / len(captures)
    signals = [signal(
        "perp", source_kind="market_data", source_name="Hyperliquid (trade.xyz) xyz:ANT perpetual", source_url="https://api.hyperliquid.xyz/info",
        source_reliability=round(held_up, 2), subtype="pre_ipo_perpetual",
        title=f"Pre-IPO perpetual xyz:ANT marks {perp['mark']:.2f} USD, {premium:.0%} above the top of the filed range",
        summary=f"Read as a first close near {perp['median_usd']:.2f} USD once the record of earlier pre-IPO perpetuals is applied.",
        event_direction=1 if premium > 0 else -1, strength=round(min(1.0, abs(premium) / 0.5), 2), confidence=round(held_up, 2), materiality=0.9,
        evidence="Share of the expected gain over the IPO price that had arrived by the first close in earlier listings: "
                 + ", ".join(f"{ticker} {capture:.2f}" for ticker, capture in captures.items()),
        reading={"mark_usd": perp["mark"], "clock_median_usd": perp["median_usd"], "clock_p10_usd": perp["p10_usd"], "clock_p90_usd": perp["p90_usd"],
                 "capture_by_listing": {ticker: round(capture, 3) for ticker, capture in captures.items()}})]
    if "market" in clocks:
        market, raw = clocks["market"], inputs["prediction_market"]
        read_at = raw["read_at"].replace("Z", "+00:00")
        signals.append(signal(
            "closing-value-odds", source_kind="market_data", source_name=raw["market"], source_url=raw["source_url"],
            source_reliability=trust["other_news"], subtype="prediction_market",
            title=f"Prediction-market odds put ANT's first-day close near {market['median_usd']:.2f} USD a share",
            summary="A lognormal fitted to the bracket odds for the first-day closing market value, converted to a share price. The odds were read from a news report.",
            event_direction=1 if market["median_usd"] > high else -1, strength=round(min(1.0, abs(market["median_usd"] / high - 1) / 0.5), 2),
            confidence=market["weight"], materiality=0.8, event_at=read_at, published_at=read_at, detected_at=read_at,
            evidence="; ".join(f"{bracket['label']}: {bracket['prob']:.1%}" for bracket in raw["brackets"]),
            reading={"brackets": raw["brackets"], "clock_median_usd": market["median_usd"]}))
    signals.append(signal(
        "day-one-employee-shares", source_kind="regulatory_filing", source_name=f"Anthropic Form {deal['filing_label']}", source_url=deal["source"],
        source_reliability=trust["exchange_filing_or_data_release"], subtype="lock_up_terms", category=["ipo", "supply"],
        title=f"Up to {millions(deal['employee_shares_sellable_from_day_one'])} employee shares may be sold from the first trading day",
        summary="Shares Eligible for Future Sale: non-founder directors, officers, employees and consultants may sell up to 10% of their holdings from day one.",
        event_direction=-1, strength=round(min(1.0, deal["employee_shares_sellable_from_day_one"] / deal["shares_offered"]), 2),
        confidence=trust["exchange_filing_or_data_release"], materiality=0.6, horizon_days=10, half_life_days=5,
        evidence=f"Compare with the {millions(deal['shares_offered'])} shares offered: first-week supply can be "
                 f"{deal['employee_shares_sellable_from_day_one'] / deal['shares_offered']:.0%} larger than the offering.",
        reading={"shares": deal["employee_shares_sellable_from_day_one"], "shares_offered": deal["shares_offered"]}))
    impacts = [{"signal_id": s["signal_id"], "issuer_id": "ANT", "link_type": "direct", "link_hops": 0, "direction": s["event_direction"],
                "exposure": None, "exposure_source": "not measured", "link_confidence": 1.0} for s in signals]
    custom = out / "signals"
    custom.mkdir(parents=True, exist_ok=True)
    (custom / "neelabhra08.signals.jsonl").write_text("".join(json.dumps(s) + "\n" for s in signals))
    (custom / "neelabhra08.impacts.jsonl").write_text("".join(json.dumps(i) + "\n" for i in impacts))
    return len(signals)


def extra_predictions(result, inputs):
    notes, deal = result["diagnostics"], inputs["deal"]
    return [
        {"statement": f"ANT prices above the top of its filed range ({usd(deal['last_range'][1])})", "by_date": deal["expected_pricing_date"],
         "probability": round(notes["ipo_price_odds"]["above_range"], 2)},
        {"statement": "ANT closes its first trading day above its IPO price", "by_date": deal["expected_first_trade_date"],
         "probability": round(notes["chance_close_above_ipo"], 2)},
        {"statement": "ANT's first-day gain over its IPO price is under 5%", "by_date": deal["expected_first_trade_date"],
         "probability": round(notes["chance_first_day_gain_under_5pct"], 2)},
        {"statement": "ANT's fifth-day close is above its first-day close", "by_date": "2026-11-25",
         "probability": round(notes["chance_day5_above_day1"], 2)},
    ]


def run_log(result, refresh_report, signal_count):
    calls, notes = result["calls"], result["diagnostics"]
    lines = [f"as of {notes['as_of']} ({notes['trading_days_to_pricing']} trading days to pricing)"]
    lines += [f"refresh {name}: {message}" for name, message in (refresh_report or {"data": "used the files committed in data/"}).items()]
    lines.append("first close by clock      p10   median      p90   weight")
    lines += [f"  {c['name']:<19} {c['p10_usd']:>8.2f} {c['median_usd']:>8.2f} {c['p90_usd']:>8.2f} {c['weight']:>8.3f}" for c in notes["clocks"]]
    perp = notes["clocks"][0]
    lines.append(f"perpetual: mark {perp['mark']}, daily vol {perp['daily_vol']:.3f}, {perp['days_to_listing']} days to listing, "
                 f"capture by earlier listing {json.dumps({k: round(v, 2) for k, v in perp['capture_by_listing'].items()})}")
    lines.append(f"comparable IPOs: {notes['comps_used']}; kernel bandwidths {json.dumps(notes['bandwidths'])}")
    lines.append(f"range basis: {notes['chance_first_range_basis']:.0%} first filed range, rest last filed range")
    lines.append("IPO price odds: " + ", ".join(f"{name} {value:.1%}" for name, value in notes["ipo_price_odds"].items()))
    lines.append(f"first-day gain: median {notes['median_first_day_gain']:+.1%}, chance under 5% {notes['chance_first_day_gain_under_5pct']:.1%}; "
                 f"day five vs day one: median {notes['median_day5_vs_day1']:+.1%}")
    lines.append("target         p10   median      p90   expected score")
    lines += [f"  {target:<11} {calls[target]['p10']:>6.2f} {calls[target]['median']:>8.2f} {calls[target]['p90']:>8.2f} {notes['expected_score'][target]:>10.3f}"
              for target in model.TARGETS]
    lines.append(f"custom signals written: {signal_count}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(HERE), help="folder for prediction.json, run_log.txt and signals/ (default: this folder)")
    parser.add_argument("--name", default="Neelabhra08/three-clocks")
    parser.add_argument("--repo", default="https://github.com/Neelabhra08/anthropic-ipo-hackathon/tree/main/submissions/Neelabhra08", help="link to the code")
    parser.add_argument("--refresh", action="store_true", help="pull live market data before forecasting")
    args = parser.parse_args()

    refresh_report = build_data.refresh() if args.refresh else None
    inputs = model.load_inputs()
    result = model.forecast(inputs)
    notes = result["diagnostics"]
    prediction = {
        "name": args.name, "method": METHOD, "repo_url": args.repo, **result["calls"],
        "thesis": write_thesis(result, inputs), "key_risks": write_risks(inputs),
        "confidence": round(sum(notes["expected_score"].values()) / len(notes["expected_score"]), 2),
        "extra_predictions": extra_predictions(result, inputs),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prediction.json").write_text(json.dumps(prediction, indent=2) + "\n")
    log = run_log(result, refresh_report, write_signals(result, inputs, out))
    (out / "run_log.txt").write_text(log)
    print(log, end="")
    print(f"wrote {out / 'prediction.json'}\ncheck it: python scripts/validate.py {out / 'prediction.json'}")


if __name__ == "__main__":
    main()
