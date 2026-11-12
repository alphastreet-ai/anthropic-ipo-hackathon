#!/usr/bin/env python3
"""Build the leaderboard. Public from day one; the formula does not change after launch.

  python scoring/score.py            # reads scoring/outcomes.json and scoring/judged.json if they exist
  python scoring/score.py --selftest

scoring/outcomes.json  {"ipo_price": 0.0, "day1_open": 0.0, "day1_close": 0.0, "day5_close": 0.0}
                       Leave a price out (or null) until it is known; the board is then provisional.
scoring/judged.json    {"<handle>": {"harness": 0-30, "idea": 0-20}}

100 points:
  Prediction 50  mean of the settled price scores, each max(0, 1 - |median - actual| / actual),
                 with the error doubled if actual falls outside your own p10..p90.
  Harness    30  judged: does it rerun, the thesis, the docs.
  Idea       20  judged: how original the approach is.
Ties go to the earlier pull request, by hand.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRICE = ["ipo_price", "day1_open", "day1_close", "day5_close"]


def price_score(answer, actual) -> float:
    err = abs(answer["median"] - actual) / actual
    if not answer["p10"] <= actual <= answer["p90"]:
        err *= 2
    return max(0.0, 1 - err)


def prediction_points(pred, outcomes) -> float:
    settled = [k for k in PRICE if outcomes.get(k)]
    return 50 * sum(price_score(pred[k], outcomes[k]) for k in settled) / len(settled) if settled else 0.0


def badges(rows, outcomes):
    """Mutates rows: adds the badge list. See BADGES.md."""
    for r in rows:
        r["badges"] = ["locked"]
    for k in PRICE:
        if outcomes.get(k) and rows:
            min(rows, key=lambda r: abs(r[k]["median"] - outcomes[k]))["badges"].append("closest-" + k.replace("_", "-"))
    if all(outcomes.get(k) for k in PRICE):
        for r in rows:
            if all(r[k]["p10"] <= outcomes[k] <= r[k]["p90"] for k in PRICE):
                r["badges"].append("calibrated")
        for key, name in (("harness", "best-harness"), ("idea", "most-original")):
            if rows and max(r[key] for r in rows) > 0:
                max(rows, key=lambda r: r[key])["badges"].append(name)
        if rows:
            rows[0]["badges"].append("winner")  # rows are already sorted by total


def build(outcomes, judged):
    rows = []
    for f in sorted((ROOT / "submissions").glob("*/prediction.json")):
        if f.parent.name == "_template":
            continue
        pred, handle = json.loads(f.read_text()), f.parent.name
        j = judged.get(handle, {})
        row = {"handle": handle, "agent": pred["agent"], "repo_url": pred["repo_url"], "thesis": pred["thesis"][:240],
               **{k: pred[k] for k in PRICE},
               "prediction": round(prediction_points(pred, outcomes), 2), "harness": j.get("harness", 0), "idea": j.get("idea", 0)}
        row["total"] = round(row["prediction"] + row["harness"] + row["idea"], 2)
        rows.append(row)
    rows.sort(key=lambda r: (-r["total"], r["handle"].lower()))
    badges(rows, outcomes)
    return rows


def selftest():
    iv = lambda m, lo, hi: {"median": m, "p10": lo, "p90": hi}
    o = {k: 100 for k in PRICE}
    assert abs(prediction_points({k: iv(100, 90, 110) for k in PRICE}, o) - 50) < 1e-9
    assert abs(price_score(iv(110, 90, 120), 100) - 0.9) < 1e-9   # 10% off, inside range
    assert abs(price_score(iv(110, 105, 120), 100) - 0.8) < 1e-9  # 10% off, outside: doubled
    assert price_score(iv(300, 250, 350), 100) == 0               # floor at zero
    # provisional: only the IPO price is known, so it carries all 50 points for now
    assert abs(prediction_points({k: iv(110, 90, 120) for k in PRICE}, {"ipo_price": 100, "day1_open": None}) - 45) < 1e-9
    assert prediction_points({k: iv(1, 1, 1) for k in PRICE}, {}) == 0
    rows = [{"handle": h, "harness": 0, "idea": 0, **{k: iv(m, m - 5, m + 5) for k in PRICE}} for h, m in (("a", 100), ("b", 120))]
    badges(rows, o)
    assert "calibrated" in rows[0]["badges"] and "closest-ipo-price" in rows[0]["badges"] and rows[1]["badges"] == ["locked"]
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    load = lambda name: json.loads(p.read_text()) if (p := ROOT / "scoring" / name).exists() else {}
    outcomes, judged = load("outcomes.json"), load("judged.json")
    rows = build(outcomes, judged)
    settled = [k for k in PRICE if outcomes.get(k)]
    board = {"settled": settled, "final": len(settled) == len(PRICE), "outcomes": outcomes, "entries": rows}
    (ROOT / "site/leaderboard.json").write_text(json.dumps(board, indent=2) + "\n")
    usd = lambda r, k: f"${r[k]['median']:g}"
    md = ["# Leaderboard", "", "Final." if board["final"] else f"Provisional. Settled so far: {', '.join(settled) or 'nothing yet'}.", "",
          "| # | Agent | IPO price | Day-1 open | Day-1 close | Day-5 close | Prediction /50 | Harness /30 | Idea /20 | Total | Badges |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {i} | [{r['agent']}]({r['repo_url']}) | {usd(r, 'ipo_price')} | {usd(r, 'day1_open')} | {usd(r, 'day1_close')} | {usd(r, 'day5_close')} "
           f"| {r['prediction']} | {r['harness']} | {r['idea']} | **{r['total']}** | {', '.join(r['badges'])} |" for i, r in enumerate(rows, 1)]
    (ROOT / "LEADERBOARD.md").write_text("\n".join(md) + "\n")
    print(f"{len(rows)} entries, settled: {settled or 'none'}")


if __name__ == "__main__":
    sys.exit(main())
