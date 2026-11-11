#!/usr/bin/env python3
"""Validate submissions/<handle>/prediction.json. No args = every submission. Exit 1 on any error."""
import json, re, sys
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schema/prediction.schema.json").read_text())
PRICE_KEYS = ["q1_ipo_price", "q4_day1_open", "q5_day1_close", "q7_close_nov30"]
HANDLE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def errors_for(pred) -> list[str]:
    errs = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in Draft202012Validator(SCHEMA).iter_errors(pred)]
    if errs:
        return errs
    for k in PRICE_KEYS:  # the schema cannot express ordering
        v = pred[k]
        if not v["p10"] <= v["median"] <= v["p90"]:
            errs.append(f"{k}: need p10 <= median <= p90")
    return errs


def main(paths: list[str]) -> int:
    files = [Path(p) for p in paths] or sorted(p for p in (ROOT / "submissions").glob("*/prediction.json") if p.parent.name != "_template")
    bad = 0
    for f in files:
        errs = [] if HANDLE.match(f.resolve().parent.name) else ["folder name must be your GitHub handle"]
        try:
            errs += errors_for(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError) as e:
            errs.append(f"cannot read JSON: {e}")
        print(("FAIL " if errs else "ok   ") + str(f))
        for e in errs:
            print("     -", e)
        bad += bool(errs)
    print(f"{len(files) - bad}/{len(files)} valid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
