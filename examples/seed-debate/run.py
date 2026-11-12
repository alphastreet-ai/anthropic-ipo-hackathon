#!/usr/bin/env python3
"""Example agent 2: a bull and a bear argue from the same context, then a judge makes the calls.

  python examples/seed-debate/run.py --out submissions/<your-handle>/ --name <your-handle>/seed-debate
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import QUESTIONS, ask, load_context, model_schema, save

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True), ap.add_argument("--name", default="you/seed-debate")
ap.add_argument("--repo", default="https://github.com/alphastreet-ai/anthropic-ipo-hackathon", help="link to your code")
args = ap.parse_args()

context = load_context()
SIDE = "Argue the {side} case for the Anthropic IPO in under 400 words: pricing versus the range, day-one trading and the first five trading days. Cite numbers from the context. Do not hedge toward the other side."
bull = ask(context, SIDE.format(side="bull"))
bear = ask(context, SIDE.format(side="bear"))
print("--- bull ---\n" + bull + "\n--- bear ---\n" + bear, file=sys.stderr)  # the run log for ENTRY.md

verdict = ask(context, f"Two analysts disagree.\n\n<bull>\n{bull}\n</bull>\n\n<bear>\n{bear}\n</bear>\n\nWeigh both, say which points survive, then commit. {QUESTIONS}", model_schema())
save(json.loads(verdict), args.name, args.out, args.repo)
