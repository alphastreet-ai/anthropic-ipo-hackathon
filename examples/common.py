"""Shared by the examples: load context/, call Claude, write and validate prediction.json."""
import copy, json, subprocess, sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schema/prediction.schema.json").read_text())
MODEL = "claude-opus-5-5"
TEXT = {".md", ".txt", ".csv", ".json", ".jsonl"}
# Structured outputs take a plain JSON schema; range and length limits are enforced by scripts/validate.py instead.
UNSUPPORTED = {"$schema", "title", "minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength", "minItems", "maxItems", "pattern"}
SET_BY_CODE = {"name", "method", "repo_url", "extra_predictions"}

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY (or an `ant auth login` profile)


def load_context() -> str:
    files = [p for p in sorted((ROOT / "context").rglob("*")) if p.is_file() and p.suffix in TEXT and p.name != "README.md" and "raw" not in p.parts]  # raw OCN pulls are too big for a prompt
    if not files:
        sys.exit("context/ has no data files yet. See context/README.md.")
    return "\n\n".join(f'<file path="{p.relative_to(ROOT)}">\n{p.read_text()}\n</file>' for p in files)


def model_schema() -> dict:
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in UNSUPPORTED}
        return [strip(v) for v in node] if isinstance(node, list) else node

    s = strip(copy.deepcopy(SCHEMA))
    s["properties"] = {k: v for k, v in s["properties"].items() if k not in SET_BY_CODE}
    s["required"] = [k for k in s["required"] if k not in SET_BY_CODE]
    return s


def ask(context: str, prompt: str, schema: dict | None = None) -> str:
    """One Claude call. The context sits in a cached system block so repeat calls in a run reuse it."""
    output_config = {"effort": "high"}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    msg = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if the model declines, the API reruns the request on a fallback model
        thinking={"type": "adaptive"},
        output_config=output_config,
        system=[
            {"type": "text", "text": "You are an equity analyst forecasting the Anthropic IPO. Use only the context files and well-known public facts. Be specific and calibrated: your 80% ranges should contain the truth four times out of five."},
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": prompt}],
    )
    if msg.stop_reason != "end_turn":
        sys.exit(f"model stopped early: {msg.stop_reason}")
    return "".join(b.text for b in msg.content if b.type == "text")


def save(prediction: dict, name: str, out_dir: str, repo_url: str, method: str = f"LLM agent, {MODEL}") -> None:
    out = Path(out_dir) / "prediction.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"name": name, "method": method, "repo_url": repo_url, **prediction}, indent=2) + "\n")
    sys.exit(subprocess.run([sys.executable, str(ROOT / "scripts/validate.py"), str(out)]).returncode)


QUESTIONS = """Make the four price calls in the schema, all in USD per share:
ipo_price, the final IPO price; day1_open and day1_close, the opening and closing price on the first trading day;
day5_close, the closing price on the fifth trading day. Each is a median with an 80% range (p10, p90).
Reason about day one and day five as a percentage move from the IPO price, then convert to prices.
Add a thesis under 300 words, three to five key risks, and an overall confidence from 0 to 1."""
