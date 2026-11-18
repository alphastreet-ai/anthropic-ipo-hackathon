#!/usr/bin/env python3
"""Refresh the market fields of data/evidence.json from live public sources, then rerun forecast.py.

  python3 submissions/poulami95/fetch_live.py                 # perp + Polymarket (+ NVDA if yfinance is installed)
  python3 submissions/poulami95/fetch_live.py --verify-comps  # also check comps.csv against Yahoo daily prices
  python3 submissions/poulami95/fetch_live.py --dry-run       # print what it found, write nothing

Standard library only (yfinance optional). A source that fails is skipped with a warning and keeps its
committed snapshot, so the forecast never breaks. Commit evidence.json after refreshing: forecast.py
reads only committed files, which is what makes a judge's rerun reproduce your prediction.json.
"""
import argparse, csv, json, re, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "data/evidence.json"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
UA = {"User-Agent": "ipo-arena-entry/1.0", "Content-Type": "application/json"}


def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=20) as r:
        return json.loads(r.read())


def hyperliquid_ant():
    """Mark price of trade.xyz's xyz:ANT on Hyperliquid's public info endpoint."""
    url = "https://api.hyperliquid.xyz/info"
    try:
        meta, ctxs = http(url, {"type": "metaAndAssetCtxs", "dex": "xyz"})
        for asset, ctx in zip(meta["universe"], ctxs):
            if asset["name"].upper() in ("XYZ:ANT", "ANT"):
                return float(ctx.get("markPx") or ctx["midPx"])
    except Exception:
        pass
    mids = http(url, {"type": "allMids", "dex": "xyz"})
    return float(next(v for k, v in mids.items() if k.upper() in ("XYZ:ANT", "ANT")))


def parse_bracket(title):
    """'<$1.25T', '$1.25T-$1.5T', '$3T+' -> (lo, hi) in USD trillions; hi None means open-ended."""
    t = title.replace(",", "")
    nums = [float(x) * (1e-3 if unit.upper() == "B" else 1) for x, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([TB])", t, re.I)]
    if not nums:
        return None
    if re.search(r"<|below|under|less", t, re.I):
        return 0.0, nums[0]
    if re.search(r"\+|above|over|more", t, re.I) or len(nums) == 1:
        return nums[0], None
    return nums[0], nums[1]


def polymarket_brackets(slug):
    events = http(f"https://gamma-api.polymarket.com/events?slug={slug}")
    out = []
    for m in events[0]["markets"]:
        if m.get("closed"):
            continue
        b = parse_bracket(m.get("groupItemTitle") or m.get("question", ""))
        if b is None:
            continue  # e.g. a "no IPO" outcome
        yes = float(json.loads(m["outcomePrices"])[0])
        out.append({"lo": b[0], "hi": b[1], "prob": round(yes, 4), "source": f"https://polymarket.com/event/{slug} ({NOW})"})
    if len(out) < 2:
        raise RuntimeError(f"only {len(out)} brackets parsed")
    return sorted(out, key=lambda r: r["lo"])


def nvda_move():
    import yfinance as yf  # optional
    h = yf.Ticker("NVDA").history(period="5d", interval="5m", prepost=True)
    d = yf.Ticker("NVDA").history(period="5d")
    last, prev_close = float(h["Close"].iloc[-1]), float(d["Close"].iloc[-1])
    return round((last / prev_close - 1) * 100, 2)


def verify_comps():
    import yfinance as yf
    rows = list(csv.DictReader(open(HERE / "data/comps.csv")))
    for r in rows:
        h = yf.Ticker(r["ticker"]).history(start=r["ipo_date"], period=None, end=None, auto_adjust=False).head(5)
        if len(h) < 5:
            print(f"  {r['company']}: not enough history")
            continue
        got = {"day1_open": h["Open"].iloc[0], "day1_close": h["Close"].iloc[0], "day5_close": h["Close"].iloc[4]}
        diffs = {k: (float(r[k]), round(float(v), 2)) for k, v in got.items() if abs(float(r[k]) - v) / v > 0.01}
        print(f"  {r['company']}: {'ok' if not diffs else 'MISMATCH ' + str(diffs)} (split-adjusted data can differ for old listings)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--polymarket-slug", default="anthropic-ipo-closing-market-cap-higher-strikes")
    ap.add_argument("--verify-comps", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ev = json.loads(EVIDENCE.read_text())
    changed = False

    try:
        px = hyperliquid_ant()
        print(f"perp xyz:ANT ${px:.2f}")
        ev["perp"]["history"].append({"date": NOW, "price": px, "source": "https://api.hyperliquid.xyz/info (xyz dex)"})
        ev["perp"].update(price=px, as_of=NOW, source="https://api.hyperliquid.xyz/info (xyz dex)")
        changed = True
    except Exception as e:
        print(f"perp: skipped, keeping ${ev['perp']['price']} ({e})", file=sys.stderr)

    try:
        br = polymarket_brackets(args.polymarket_slug)
        for b in br:
            print(f"polymarket {b['lo']}-{b['hi'] or 'inf'}T: {b['prob']:.1%}")
        ev["polymarket_close_mcap"].update(brackets_usd_tn=br, as_of=NOW)
        changed = True
    except Exception as e:
        print(f"polymarket: skipped, keeping snapshot ({e})", file=sys.stderr)

    try:
        mv = nvda_move()
        print(f"NVDA vs last close: {mv:+.2f}%")
        ev["nvidia"].update(after_hours_move_pct=mv, source=f"yfinance NVDA pre/post-market vs prior close ({NOW})")
        changed = True
    except Exception as e:
        print(f"nvidia: skipped ({e}); set nvidia.after_hours_move_pct by hand from any quote page if you like", file=sys.stderr)

    if args.verify_comps:
        try:
            verify_comps()
        except Exception as e:
            print(f"comps check skipped ({e})", file=sys.stderr)

    if changed and not args.dry_run:
        ev["as_of"] = NOW
        EVIDENCE.write_text(json.dumps(ev, indent=2) + "\n")
        print(f"updated {EVIDENCE}. Now run: python3 {HERE.relative_to(Path.cwd()) if HERE.is_relative_to(Path.cwd()) else HERE}/forecast.py")


if __name__ == "__main__":
    main()
