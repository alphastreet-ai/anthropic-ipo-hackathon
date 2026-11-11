#!/usr/bin/env python3
"""Score every submission against the real outcome. Public from day one.

  python scoring/score.py --outcomes outcomes.json [--judged judged.json]

outcomes.json: {"q1_ipo_price": 0.0, "q2_above_range": true, "q3_upsized": false,
                "q4_day1_open": 0.0, "q5_day1_close": 0.0, "q6_close_above": true, "q7_close_nov30": 0.0}
judged.json (optional): {"<handle>": 0-30}

Accuracy (70 points) is the mean of seven per-question scores in [0, 1]:
  probability: 1 - (p - outcome)^2                      (Brier)
  price:       max(0, 1 - |median - actual| / actual)   (percentage error),
               with the error doubled if actual falls outside your own p10..p90.
Judged (30 points) is added from judged.json. Ties are broken by earlier pull request, by hand.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROB = ["q2_above_range", "q3_upsized", "q6_close_above"]
PRICE = ["q1_ipo_price", "q4_day1_open", "q5_day1_close", "q7_close_nov30"]


def question_score(key, answer, actual) -> float:
    if key in PROB:
        return 1 - (answer - float(actual)) ** 2
    err = abs(answer["median"] - actual) / actual
    if not answer["p10"] <= actual <= answer["p90"]:
        err *= 2
    return max(0.0, 1 - err)


def accuracy(pred, outcomes) -> float:
    return 70 * sum(question_score(k, pred[k], outcomes[k]) for k in PROB + PRICE) / 7


def selftest():
    o = {"q1_ipo_price": 100, "q2_above_range": True, "q3_upsized": False, "q4_day1_open": 100, "q5_day1_close": 100, "q6_close_above": True, "q7_close_nov30": 100}
    iv = lambda m, lo, hi: {"median": m, "p10": lo, "p90": hi}
    perfect = {**{k: iv(100, 90, 110) for k in PRICE}, "q2_above_range": 1, "q3_upsized": 0, "q6_close_above": 1}
    assert abs(accuracy(perfect, o) - 70) < 1e-9
    assert abs(question_score("q1_ipo_price", iv(110, 90, 120), 100) - 0.9) < 1e-9   # 10% off, inside range
    assert abs(question_score("q1_ipo_price", iv(110, 105, 120), 100) - 0.8) < 1e-9  # 10% off, outside: doubled
    assert question_score("q1_ipo_price", iv(300, 250, 350), 100) == 0               # floor at zero
    assert abs(question_score("q2_above_range", 0.5, True) - 0.75) < 1e-9
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outcomes"), ap.add_argument("--judged"), ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.outcomes:
        ap.error("--outcomes is required")
    outcomes = json.loads(Path(a.outcomes).read_text())
    judged = json.loads(Path(a.judged).read_text()) if a.judged else {}
    rows = []
    for f in sorted((ROOT / "submissions").glob("*/prediction.json")):
        if f.parent.name == "_template":
            continue
        pred, handle = json.loads(f.read_text()), f.parent.name
        acc = accuracy(pred, outcomes)
        rows.append({"handle": handle, "agent": pred["agent"], "accuracy": round(acc, 2), "judged": judged.get(handle, 0), "total": round(acc + judged.get(handle, 0), 2)})
    rows.sort(key=lambda r: -r["total"])
    (ROOT / "leaderboard.json").write_text(json.dumps(rows, indent=2) + "\n")
    for i, r in enumerate(rows, 1):
        print(f"{i:>3}  {r['total']:>6.2f}  (acc {r['accuracy']:.2f} + judged {r['judged']})  {r['handle']}  {r['agent']}")


if __name__ == "__main__":
    sys.exit(main())
