#!/usr/bin/env python3
"""Seed agent 1: one call. Reads context/, writes a valid prediction.json.

  python agents/seed-simple/run.py --out submissions/<your-handle>/ --agent <your-handle>/seed-simple
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import QUESTIONS, ask, load_context, model_schema, save

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True), ap.add_argument("--agent", default="you/seed-simple")
ap.add_argument("--repo", default="https://github.com/alphastreet-ai/anthropic-ipo-hackathon", help="link to your agent's code")
args = ap.parse_args()

answer = ask(load_context(), QUESTIONS, model_schema())
save(json.loads(answer), args.agent, args.out, args.repo)
