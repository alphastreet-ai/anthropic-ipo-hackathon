import copy
import importlib.util
import math
import sys
import unittest
from datetime import date
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model

DRAWS = 6000


def organiser_score():
    spec = importlib.util.spec_from_file_location("organiser_score", ROOT / "scoring" / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.price_score


class Helpers(unittest.TestCase):
    def test_weighted_quantile_follows_the_weights(self):
        self.assertEqual(model.weighted_quantile([1, 2, 3], [1, 1, 1], 0.5), 2)
        self.assertEqual(model.weighted_quantile([1, 2, 3], [10, 1, 1], 0.5), 1)
        self.assertEqual(model.weighted_quantile([1, 2, 3], [1, 1, 10], 0.5), 3)

    def test_trading_days_skip_the_weekend(self):
        self.assertEqual(model.trading_days_between(date(2026, 11, 15), date(2026, 11, 18)), 3)
        self.assertEqual(model.trading_days_between(date(2026, 11, 18), date(2026, 11, 18)), 0)

    def test_score_matches_the_organisers_formula(self):
        official = organiser_score()
        for median in (60, 84, 100, 140):
            for low, high in ((50, 70), (80, 120), (99, 101)):
                answer = {"median": median, "p10": min(low, median), "p90": max(high, median)}
                for actual in (55, 84, 100, 131):
                    self.assertAlmostEqual(model.price_score(answer, actual), official(answer, actual))


class Clocks(unittest.TestCase):
    def test_market_clock_recovers_a_known_distribution(self):
        shares, centre, sd = 10e9, math.log(100), 0.2
        edges_usd_trn = [0.9, 1.2]
        cumulative = [NormalDist(centre, sd).cdf(math.log(edge * 1e12 / shares)) for edge in edges_usd_trn]
        brackets = [{"upper_usd_trn": edges_usd_trn[0], "prob": cumulative[0]}, {"upper_usd_trn": edges_usd_trn[1], "prob": cumulative[1] - cumulative[0]},
                    {"upper_usd_trn": None, "prob": 1 - cumulative[1]}]
        clock = model.market_clock({"prediction_market": {"brackets": brackets, "read_at": "x", "source_url": "x"},
                                    "deal": {"shares_outstanding_after": shares}})
        self.assertAlmostEqual(clock["center"], centre, places=6)
        self.assertAlmostEqual(clock["sd"], sd, places=6)

    def test_each_earlier_listing_counts_once_however_many_readings_it_has(self):
        precedents = [{"ticker": "A", "capture": 1.0}] * 6 + [{"ticker": "B", "capture": 0.0}]
        clock = model.perp_clock({"precedents": precedents, "perp_closes": [100, 101, 99], "perp_mark": 100,
                                  "deal": {"expected_first_trade_date": "2026-11-19"}, "as_of": date(2026, 11, 15)})
        weight = {capture: 0.0 for capture in (0.0, 1.0)}
        for capture, w in zip(clock["captures"], clock["weights"]):
            weight[capture] += w
        self.assertAlmostEqual(weight[0.0], weight[1.0])

    def test_without_earlier_listings_the_perpetual_is_taken_at_face_value(self):
        clock = model.perp_clock({"precedents": [], "perp_closes": [100, 101], "perp_mark": 100,
                                  "deal": {"expected_first_trade_date": "2026-11-19"}, "as_of": date(2026, 11, 15)})
        self.assertEqual(clock["captures"], [1.0])


class Forecast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = model.load_inputs()
        cls.inputs["as_of"] = date(2026, 11, 15)
        cls.base = model.forecast(cls.inputs, draws=DRAWS)

    def on_pricing_day(self, **deal_changes):
        inputs = copy.deepcopy(self.inputs)
        inputs["as_of"] = date.fromisoformat(inputs["deal"]["expected_pricing_date"])
        inputs["deal"].update(deal_changes)
        return model.forecast(inputs, draws=DRAWS)

    def test_every_call_is_ordered_and_positive(self):
        for target in model.TARGETS:
            call = self.base["calls"][target]
            self.assertTrue(0 < call["p10"] <= call["median"] <= call["p90"], target)

    def test_same_inputs_give_the_same_file(self):
        self.assertEqual(model.forecast(self.inputs, draws=DRAWS)["calls"], self.base["calls"])

    def test_ipo_price_is_in_whole_dollars(self):
        for value in self.base["calls"]["ipo_price"].values():
            self.assertEqual(value, round(value))

    def test_without_a_new_filing_the_price_stays_under_the_rule_430a_cap(self):
        result = self.on_pricing_day()
        deal = self.inputs["deal"]
        cap = deal["max_price_over_range_top_without_new_filing"] * deal["last_range"][1]
        self.assertLessEqual(result["calls"]["ipo_price"]["p90"], cap)
        self.assertEqual(result["diagnostics"]["chance_first_range_basis"], 0.0)

    def test_a_range_refiled_on_the_eve_of_pricing_rules_out_a_formal_increase(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["as_of"] = date(2026, 11, 17)
        inputs["deal"]["last_range_filed_on"] = "2026-11-10"
        self.assertEqual(model.forecast(inputs, draws=DRAWS)["diagnostics"]["chance_first_range_basis"], 0.5)
        inputs["deal"]["last_range_filed_on"] = "2026-11-17"
        self.assertEqual(model.forecast(inputs, draws=DRAWS)["diagnostics"]["chance_first_range_basis"], 0.0)

    def test_a_raised_range_lifts_the_ipo_price_call(self):
        unchanged = self.on_pricing_day()["calls"]["ipo_price"]["median"]
        raised = self.on_pricing_day(last_range=[80.0, 86.0])["calls"]["ipo_price"]["median"]
        self.assertGreater(raised, unchanged)

    def test_a_higher_perpetual_lifts_the_trading_calls(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["perp_mark"] *= 1.2
        higher = model.forecast(inputs, draws=DRAWS)["calls"]
        for target in ("day1_open", "day1_close", "day5_close"):
            self.assertGreater(higher[target]["median"], self.base["calls"][target]["median"], target)

    def test_a_fixed_price_deal_keeps_its_price(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["deal"] = {"fixed_price": 135.0, "expected_first_trade_date": "2026-11-19", "expected_pricing_date": "2026-11-18"}
        inputs["prediction_market"] = None
        call = model.forecast(inputs, draws=DRAWS)["calls"]["ipo_price"]
        self.assertEqual((call["p10"], call["median"], call["p90"]), (135.0, 135.0, 135.0))


if __name__ == "__main__":
    unittest.main()
