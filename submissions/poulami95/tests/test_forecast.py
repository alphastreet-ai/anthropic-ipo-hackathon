"""Checks for forecast.py. Run from the repo root:

  python3 -m unittest discover -s submissions/poulami95/tests -v

Standard library only; uses 20,000 paths so the suite runs in a few seconds.
"""
import json, math, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import forecast as F  # noqa: E402

F.CFG["n_paths"] = 20_000


def run(ev=None):
    ev = ev or F.load_evidence()
    cs = F.comps_stats()
    _, _, vm = F.vix_multiplier()
    paths = F.simulate(ev, cs, vm, 0.0)
    w, br, q, model = F.polymarket_weights(paths, ev)
    return ev, paths, w, br, q, model


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev, cls.paths, cls.w, cls.br, cls.q, cls.model = run()
        cls.res = F.summarise(cls.paths, cls.w)

    def test_deterministic(self):
        _, paths2, w2, *_ = run()
        self.assertEqual(F.summarise(paths2, w2), self.res)

    def test_quantiles_ordered_and_positive(self):
        for k in F.KEYS:
            v = self.res[k]
            self.assertTrue(0 < v["p10"] <= v["median"] <= v["p90"], (k, v))

    def test_ipo_price_respects_book_and_perp_cap(self):
        hi, perp = self.ev["offering"]["range_high"], self.ev["perp"]["price"]
        cap = perp / (1 + F.CFG["min_pop_vs_perp"])
        for ipo, *_ in self.paths:
            self.assertGreaterEqual(ipo, hi - 4 - 1e-9)
            self.assertLessEqual(ipo, max(hi, cap) + 1e-9)

    def test_open_floor_holds(self):
        for ipo, opn, *_ in self.paths:
            self.assertGreaterEqual(opn, ipo * F.CFG["open_floor"] - 1e-9)

    def test_weights_are_a_distribution(self):
        self.assertAlmostEqual(sum(self.w), 1.0, places=9)
        self.assertTrue(all(x >= 0 for x in self.w))

    def test_polymarket_tilt_moves_toward_market(self):
        """After tilting, each close bracket's probability sits between the model's and the market's."""
        for i, (lo, hi, _) in enumerate(self.br):
            tilted = sum(wi for p, wi in zip(self.paths, self.w) if lo <= p[2] < hi)
            a, b = sorted((self.model[i], self.q[i]))
            self.assertTrue(a - 0.01 <= tilted <= b + 0.01, (i, self.model[i], tilted, self.q[i]))

    def test_higher_perp_raises_open(self):
        ev = json.loads(json.dumps(self.ev))
        ev["perp"]["price"] *= 1.10
        _, paths, w, *_ = run(ev)
        self.assertGreater(F.summarise(paths, w)["day1_open"]["median"], self.res["day1_open"]["median"])

    def test_bracket_parser(self):
        sys.path.insert(0, str(HERE))
        import fetch_live as L
        self.assertEqual(L.parse_bracket("<$1.25T"), (0.0, 1.25))
        self.assertEqual(L.parse_bracket("$1.25T-$1.5T"), (1.25, 1.5))
        self.assertEqual(L.parse_bracket("$3T+"), (3.0, None))
        self.assertIsNone(L.parse_bracket("No IPO"))

    def test_committed_prediction_matches_full_run_shape(self):
        p = json.loads((HERE / "prediction.json").read_text())
        for k in F.KEYS:
            self.assertTrue(math.isfinite(p[k]["median"]))
        self.assertLessEqual(len(p["thesis"]), 2500)


if __name__ == "__main__":
    unittest.main()
