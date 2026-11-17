#!/usr/bin/env python3
"""Refresh the entry's data files in data/ from public sources."""
import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources

DATA = Path(__file__).resolve().parent / "data"
PERP_DEX = "xyz"
PRE_IPO_PERPS = ["CBRS", "QNT", "SPCX"]
CALIBRATION_LAGS = range(1, 8)
LIQUIDITY_WINDOW_DAYS = 5


def read_csv(name):
    with open(DATA / name, newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(name, header, rows):
    with open(DATA / name, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def first_week(ticker, first_trade_date):
    daily = sources.yahoo_daily(ticker, first_trade_date, first_trade_date + timedelta(days=16))
    bars = [bar for bar in daily["bars"] if bar["date"] >= first_trade_date]
    if len(bars) < 5:
        raise RuntimeError(f"{ticker}: only {len(bars)} bars in the first week")
    factor = sources.yahoo_split_factor(ticker, bars[0]["date"])
    return {"first_bar": bars[0]["date"], "open": bars[0]["open"] * factor,
            "close1": bars[0]["close"] * factor, "close5": bars[4]["close"] * factor}


def build_comp_paths():
    rows, skipped = [], []
    for comp in read_csv("comps.csv"):
        listed = date.fromisoformat(comp["first_trade_date"])
        ipo_price = float(comp["ipo_price"])
        try:
            week = first_week(comp["ticker"], listed)
        except Exception as error:
            skipped.append(f"{comp['ticker']} ({error})")
            continue
        ratios = [week[key] / ipo_price for key in ("open", "close1", "close5")]
        # A first bar on another date means the vendor lacks day one; a ratio this far from 1 means a wrong ticker or split factor.
        if not 0.4 < ratios[0] < 5 or week["first_bar"] != listed:
            skipped.append(f"{comp['ticker']} (first bar {week['first_bar']}, open/IPO {ratios[0]:.2f})")
            continue
        rows.append([comp["ticker"], comp["first_trade_date"], *(f"{ratio:.4f}" for ratio in ratios)])
        time.sleep(0.2)
    write_csv("comp_paths.csv", ["ticker", "first_trade_date", "open_over_ipo", "close1_over_ipo", "close5_over_ipo"], rows)
    return f"{len(rows)} comps" + (f"; skipped: {'; '.join(skipped)}" if skipped else "")


def build_perp_calibration():
    rows = []
    for ticker in PRE_IPO_PERPS:
        listed = sources.yahoo_daily(ticker, date.today() - timedelta(days=7), date.today())["first_trade_date"]
        week = first_week(ticker, listed)
        candles = {c["date"]: c for c in sources.hyperliquid_candles(f"{PERP_DEX}:{ticker}", listed - timedelta(days=30), listed)}
        window = [candles[listed - timedelta(days=d)] for d in range(1, LIQUIDITY_WINDOW_DAYS + 1) if listed - timedelta(days=d) in candles]
        liquidity = sum(c["volume"] * c["close"] for c in window) / len(window)
        for lag in CALIBRATION_LAGS:
            candle = candles.get(listed - timedelta(days=lag))
            if candle is None:
                continue
            rows.append([ticker, listed.isoformat(), lag, f"{candle['close']:.3f}", f"{liquidity:.0f}",
                         *(f"{week[key] / candle['close']:.4f}" for key in ("open", "close1", "close5"))])
    write_csv("perp_calibration.csv", ["ticker", "first_trade_date", "lag_days", "perp_close", "perp_usd_per_day",
                                       "open_over_perp", "close1_over_perp", "close5_over_perp"], rows)
    return f"{len(rows)} rows for {', '.join(PRE_IPO_PERPS)}"


def build_perp_ant():
    deal = json.loads((DATA / "deal.json").read_text())
    coin = f"{PERP_DEX}:{deal['ticker']}"
    candles = sources.hyperliquid_candles(coin, date(2026, 10, 1), date.today() + timedelta(days=1))
    quote = sources.hyperliquid_universe(PERP_DEX)[coin]
    now = datetime.now(timezone.utc)
    # The last candle is still forming, so it is kept out of the history and reported as the live quote instead.
    closed = [c for c in candles if c["date"] < now.date()]
    write_csv("perp_ant.csv", ["date", "close", "volume_contracts"], [[c["date"].isoformat(), c["close"], f"{c['volume']:.0f}"] for c in closed])
    window = closed[-LIQUIDITY_WINDOW_DAYS:]
    snapshot = {"coin": coin, "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mark": quote["mark"], "oracle": quote["oracle"],
                "open_interest_contracts": quote["open_interest"], "funding_per_hour": quote["funding"],
                "usd_per_day": round(sum(c["volume"] * c["close"] for c in window) / len(window)),
                "source": "https://api.hyperliquid.xyz/info (metaAndAssetCtxs, candleSnapshot)"}
    (DATA / "market_snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    return f"{len(closed)} daily closes, mark {quote['mark']}"


STEPS = {"comps": build_comp_paths, "calibration": build_perp_calibration, "perp": build_perp_ant}


def refresh(only=None):
    report = {}
    for name, step in STEPS.items():
        if only and name != only:
            continue
        try:
            report[name] = step()
        except Exception as error:
            report[name] = f"FAILED, kept the committed file ({type(error).__name__}: {error})"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=sorted(STEPS))
    outcome = refresh(parser.parse_args().only)
    for name, message in outcome.items():
        print(f"{name}: {message}")
    sys.exit(1 if all(message.startswith("FAILED") for message in outcome.values()) else 0)
