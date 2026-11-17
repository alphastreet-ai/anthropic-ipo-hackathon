import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import NormalDist

DATA = Path(__file__).resolve().parent / "data"
DRAWS = 40000
SEED = 20261118
BANDWIDTHS = (0.08, 0.12, 0.18, 0.25, 0.35, 0.5)
# The smoothest fit within this margin of the best leave-one-out error wins, so noise does not pick the bandwidth.
BANDWIDTH_TOLERANCE = 1.05
LARGEST_DEALS = 15
SMOOTHING = 0.25
QUANTILES = {"p10": 0.10, "median": 0.50, "p90": 0.90}
TARGETS = ("ipo_price", "day1_open", "day1_close", "day5_close")
# Chance that a formal range change is still to come, by trading days left before pricing.
# In the large deals that raised their range it happened one to three trading days before pricing.
RANGE_CHANGE_STILL_POSSIBLE = {0: 0.0, 1: 0.5}


def read_csv(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def load_inputs(data_dir=DATA):
    data_dir = Path(data_dir)
    paths = {row["ticker"]: row for row in read_csv(data_dir / "comp_paths.csv")}
    comps, ipo_prices = [], {}
    for row in read_csv(data_dir / "comps.csv"):
        ipo_prices[row["ticker"]] = float(row["ipo_price"])
        path = paths.get(row["ticker"])
        if path is None:
            continue
        comps.append({"ticker": row["ticker"], "listed": date.fromisoformat(row["first_trade_date"]), "ipo": float(row["ipo_price"]),
                      "raised": float(row["raised_usd_bn"]),
                      "first_top": float(row["first_range_high"]) if row["first_range_high"] else None,
                      "last_top": float(row["last_range_high"]) if row["last_range_high"] else None,
                      "open": float(path["open_over_ipo"]), "close1": float(path["close1_over_ipo"]),
                      "close5": float(path["close5_over_ipo"])})
    precedents = []
    for row in read_csv(data_dir / "perp_calibration.csv"):
        expected_gain = math.log(float(row["perp_close"]) / ipo_prices[row["ticker"]])
        actual_gain = expected_gain + math.log(float(row["close1_over_perp"]))
        precedents.append({"ticker": row["ticker"], "listed": date.fromisoformat(row["first_trade_date"]), "capture": actual_gain / expected_gain})
    snapshot = json.loads((data_dir / "market_snapshot.json").read_text())
    market_file = data_dir / "prediction_market.json"
    return {
        "deal": json.loads((data_dir / "deal.json").read_text()),
        "comps": comps,
        "precedents": precedents,
        "perp_closes": [float(row["close"]) for row in read_csv(data_dir / "perp_ant.csv")],
        "perp_mark": snapshot["mark"],
        "prediction_market": json.loads(market_file.read_text()) if market_file.exists() else None,
        "as_of": datetime.strptime(snapshot["read_at"], "%Y-%m-%dT%H:%M:%SZ").date(),
    }


def weighted_quantile(values, weights, q):
    ordered = sorted(zip(values, weights))
    target = q * sum(weights)
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= target:
            return value
    return ordered[-1][0]


def weighted_sd(values, weights):
    total = sum(weights)
    mean = sum(v * w for v, w in zip(values, weights)) / total
    return math.sqrt(sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / total)


def median(values):
    return sorted(values)[len(values) // 2]


def trading_days_between(start, end):
    """Weekdays after `start` up to and including `end`."""
    return sum(1 for offset in range(1, (end - start).days + 1) if (start + timedelta(days=offset)).weekday() < 5)


class Conditional:
    def __init__(self, xs, ys, smoothing=SMOOTHING):
        self.xs, self.ys, self.smoothing = list(xs), list(ys), smoothing
        errors = {bandwidth: self._leave_one_out_error(bandwidth) for bandwidth in BANDWIDTHS}
        self.bandwidth = max(b for b, error in errors.items() if error <= BANDWIDTH_TOLERANCE * min(errors.values()))
        self._cache = {}

    def _weights(self, x0, bandwidth, skip=None):
        return [0.0 if i == skip else math.exp(-0.5 * ((x - x0) / bandwidth) ** 2) for i, x in enumerate(self.xs)]

    def _leave_one_out_error(self, bandwidth):
        errors = []
        for i, (x, y) in enumerate(zip(self.xs, self.ys)):
            weights = self._weights(x, bandwidth, skip=i)
            if sum(weights) > 1e-9:
                errors.append(abs(weighted_quantile(self.ys, weights, 0.5) - y))
        return sum(errors) / len(errors)

    def draw(self, x0, rng):
        key = round(x0, 2)
        if key not in self._cache:
            weights = self._weights(key, self.bandwidth)
            if sum(weights) < 1e-9:  # far outside the data: fall back to the nearest deals
                nearest = sorted(range(len(self.xs)), key=lambda i: abs(self.xs[i] - key))[:5]
                weights = [1.0 if i in nearest else 0.0 for i in range(len(self.xs))]
            self._cache[key] = (weights, weighted_sd(self.ys, weights))
        weights, spread = self._cache[key]
        return rng.choices(self.ys, weights)[0] + rng.gauss(0, self.smoothing * spread)

    def median(self, x0):
        return weighted_quantile(self.ys, self._weights(x0, self.bandwidth), 0.5)


def perp_clock(inputs):
    """Capture 1 means the stock closed where the perpetual was, 0 means it closed at its IPO price."""
    by_listing = {}
    for row in inputs["precedents"]:
        by_listing.setdefault(row["ticker"], []).append(row["capture"])
    captures, weights = [1.0], [1.0]
    if by_listing:
        captures = [capture for rows in by_listing.values() for capture in rows]
        weights = [1 / len(rows) for rows in by_listing.values() for _ in rows]
    closes = inputs["perp_closes"][-21:]
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    daily_vol = math.sqrt(sum(r * r for r in returns) / len(returns)) if returns else 0.0
    days = max(1, (date.fromisoformat(inputs["deal"]["expected_first_trade_date"]) - inputs["as_of"]).days)
    return {"captures": captures, "weights": weights, "drift_sd": daily_vol * math.sqrt(days),
            "detail": {"mark": inputs["perp_mark"], "daily_vol": daily_vol, "days_to_listing": days,
                       "capture_by_listing": {ticker: median(rows) for ticker, rows in by_listing.items()}}}


def market_clock(inputs):
    market = inputs["prediction_market"]
    if not market:
        return None
    shares = inputs["deal"]["shares_outstanding_after"]
    total = sum(bracket["prob"] for bracket in market["brackets"])
    running, points = 0.0, []
    for bracket in market["brackets"][:-1]:
        running += bracket["prob"] / total
        points.append((math.log(bracket["upper_usd_trn"] * 1e12 / shares), NormalDist().inv_cdf(running)))
    (low_x, low_z), (high_x, high_z) = points[0], points[-1]
    sd = (high_x - low_x) / (high_z - low_z)
    return {"center": low_x - low_z * sd, "sd": sd, "detail": {"read_at": market["read_at"], "source_url": market["source_url"]}}


def history_clock(inputs):
    largest = sorted(inputs["comps"], key=lambda c: -c["raised"])[:LARGEST_DEALS]
    gains = [math.log(c["close1"]) for c in largest]
    return {"gains": gains, "spread": weighted_sd(gains, [1.0] * len(gains)),
            "detail": {"deals": [c["ticker"] for c in largest], "median_gain": math.exp(median(gains)) - 1,
                       "flat_or_down": sum(1 for gain in gains if gain < math.log(1.05))}}


def price_score(answer, actual):
    """Must stay identical to price_score in scoring/score.py."""
    error = abs(answer["median"] - actual) / actual
    if not answer["p10"] <= actual <= answer["p90"]:
        error *= 2
    return max(0.0, 1 - error)


def forecast(inputs, draws=DRAWS, seed=SEED):
    rng = random.Random(seed)
    deal, comps = inputs["deal"], inputs["comps"]
    fixed_price = deal.get("fixed_price")
    perp, market, history = perp_clock(inputs), market_clock(inputs), history_clock(inputs)
    clock_names = ["perp", "history"] + (["market"] if market else [])

    ranged = [c for c in comps if c["first_top"]]
    price_given_level = {
        basis: Conditional([math.log(c["close1"] * c["ipo"] / c[top]) for c in ranged], [math.log(c["ipo"] / c[top]) for c in ranged], smoothing=0)
        for basis, top in (("first", "first_top"), ("last", "last_top"))
    }  # no smoothing: IPO prices are whole dollars and cluster on the top of the range
    gains = [math.log(c["close1"]) for c in comps]
    open_given_gain = Conditional(gains, [math.log(c["open"] / c["close1"]) for c in comps])
    day5_given_gain = Conditional(gains, [math.log(c["close5"] / c["close1"]) for c in comps])

    pricing_date = date.fromisoformat(deal["expected_pricing_date"])
    days_to_pricing = trading_days_between(inputs["as_of"], pricing_date)
    chance_first_basis = 0.0
    if not fixed_price:
        first_top, last_top = deal["first_range"][1], deal["last_range"][1]
        price_cap = math.floor(deal["max_price_over_range_top_without_new_filing"] * last_top)
        refiled = deal.get("last_range_filed_on")
        # A range refiled unchanged within a trading day of pricing leaves no time for a formal increase.
        refiled_late = bool(refiled) and trading_days_between(date.fromisoformat(refiled), pricing_date) <= 1
        if deal["first_range"] == deal["last_range"] and not refiled_late:
            chance_first_basis = RANGE_CHANGE_STILL_POSSIBLE.get(days_to_pricing, 1.0)

    def price_for(level):
        if fixed_price:
            return fixed_price
        if rng.random() < chance_first_basis:
            return max(1, round(first_top * math.exp(price_given_level["first"].draw(math.log(level / first_top), rng))))
        price = round(last_top * math.exp(price_given_level["last"].draw(math.log(level / last_top), rng)))
        return max(1, min(price, price_cap))

    samples = {target: [] for target in TARGETS}
    by_clock = {name: [] for name in clock_names}
    for _ in range(draws):
        clock = rng.choice(clock_names)
        expected = inputs["perp_mark"] * math.exp(rng.gauss(0, perp["drift_sd"]))
        if clock == "market":
            close1 = math.exp(rng.gauss(market["center"], market["sd"]))
            ipo = price_for(close1)
        else:
            ipo = price_for(expected)
            if clock == "perp":
                close1 = ipo * math.exp(rng.choices(perp["captures"], perp["weights"])[0] * math.log(expected / ipo))
            else:
                close1 = ipo * math.exp(rng.choice(history["gains"]) + rng.gauss(0, SMOOTHING * history["spread"]))
        gain = math.log(close1 / ipo)
        by_clock[clock].append(close1)
        samples["ipo_price"].append(float(ipo))
        samples["day1_close"].append(close1)
        samples["day1_open"].append(close1 * math.exp(open_given_gain.draw(gain, rng)))
        samples["day5_close"].append(close1 * math.exp(day5_given_gain.draw(gain, rng)))

    calls = {}
    for target in TARGETS:
        ordered = sorted(samples[target])
        calls[target] = {name: round(ordered[min(draws - 1, int(q * draws))], 2) for name, q in QUANTILES.items()}

    low, high = (fixed_price, fixed_price) if fixed_price else deal["last_range"]
    prices, closes = samples["ipo_price"], samples["day1_close"]
    share = lambda test: sum(1 for value in prices if test(value)) / draws
    details = {"perp": perp["detail"], "history": history["detail"], "market": market["detail"] if market else None}
    diagnostics = {
        "as_of": inputs["as_of"].isoformat(),
        "clocks": [{"name": name, "weight": round(1 / len(clock_names), 3), "median_usd": round(median(by_clock[name]), 2),
                    "p10_usd": round(sorted(by_clock[name])[len(by_clock[name]) // 10], 2),
                    "p90_usd": round(sorted(by_clock[name])[len(by_clock[name]) * 9 // 10], 2), **details[name]} for name in clock_names],
        "trading_days_to_pricing": days_to_pricing,
        "chance_first_range_basis": chance_first_basis,
        "bandwidths": {"price_first": price_given_level["first"].bandwidth, "price_last": price_given_level["last"].bandwidth,
                       "open": open_given_gain.bandwidth, "day5": day5_given_gain.bandwidth},
        "ipo_price_odds": {"below_range": share(lambda p: p < low), "inside_range_below_top": share(lambda p: low <= p < high),
                           "at_top": share(lambda p: p == high), "above_range": share(lambda p: p > high)},
        "median_first_day_gain": median([c / p - 1 for c, p in zip(closes, prices)]),
        "chance_first_day_gain_under_5pct": sum(1 for c, p in zip(closes, prices) if c < 1.05 * p) / draws,
        "chance_close_above_ipo": sum(1 for c, p in zip(closes, prices) if c > p) / draws,
        "median_day5_vs_day1": median([d / c - 1 for d, c in zip(samples["day5_close"], closes)]),
        "chance_day5_above_day1": sum(1 for d, c in zip(samples["day5_close"], closes) if d > c) / draws,
        "expected_score": {target: sum(price_score(calls[target], value) for value in samples[target]) / draws for target in TARGETS},
        "comps_used": len(comps),
    }
    return {"calls": calls, "diagnostics": diagnostics}
