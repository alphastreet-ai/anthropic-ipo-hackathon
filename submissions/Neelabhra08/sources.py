import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
HYPERLIQUID = "https://api.hyperliquid.xyz/info"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
BROWSER_AGENT = "Mozilla/5.0"


def _get_json(url, body=None, headers=None, timeout=30, retries=2):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"User-Agent": BROWSER_AGENT, **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429:
                raise
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))


def _epoch(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def yahoo_daily(ticker, start, end, with_splits=False):
    """Prices come back adjusted for every later split."""
    url = f"{YAHOO}{ticker}?period1={_epoch(start)}&period2={_epoch(end)}&interval=1d"
    if with_splits:
        url += "&events=split"
    chart = _get_json(url)["chart"]
    if chart.get("error"):
        raise RuntimeError(f"yahoo {ticker}: {chart['error']}")
    result = chart["result"][0]
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, stamp in enumerate(result.get("timestamp") or []):
        if quote["open"][i] is None or quote["close"][i] is None:
            continue
        bars.append({"date": datetime.fromtimestamp(stamp, UTC).date(), "open": quote["open"][i], "close": quote["close"][i]})
    splits = [(datetime.fromtimestamp(s["date"], UTC).date(), s["numerator"] / s["denominator"])
              for s in (result.get("events", {}).get("splits") or {}).values()]
    first_trade = result["meta"].get("firstTradeDate")
    return {"bars": bars, "splits": sorted(splits),
            "first_trade_date": datetime.fromtimestamp(first_trade, UTC).date() if first_trade else None}


def yahoo_split_factor(ticker, since):
    """Multiply an adjusted price by this to get the price as traded."""
    url = f"{YAHOO}{ticker}?period1={_epoch(since)}&period2={_epoch(date.today() + timedelta(days=1))}&interval=3mo&events=split"
    chart = _get_json(url)["chart"]
    if chart.get("error"):
        raise RuntimeError(f"yahoo {ticker}: {chart['error']}")
    factor = 1.0
    for split in (chart["result"][0].get("events", {}).get("splits") or {}).values():
        if datetime.fromtimestamp(split["date"], UTC).date() > since:
            factor *= split["numerator"] / split["denominator"]
    return factor


def hyperliquid_universe(dex="xyz"):
    meta, contexts = _get_json(HYPERLIQUID, {"type": "metaAndAssetCtxs", "dex": dex}, {"Content-Type": "application/json"})
    return {asset["name"]: {"mark": float(ctx["markPx"]), "oracle": float(ctx["oraclePx"]),
                            "open_interest": float(ctx["openInterest"]), "day_volume_usd": float(ctx["dayNtlVlm"]),
                            "funding": float(ctx["funding"])}
            for asset, ctx in zip(meta["universe"], contexts)}


def hyperliquid_candles(coin, start, end, interval="1d"):
    """A daily candle dated D covers the UTC day D and closes at 00:00 UTC on D+1."""
    body = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                              "startTime": _epoch(start) * 1000, "endTime": _epoch(end) * 1000}}
    rows = _get_json(HYPERLIQUID, body, {"Content-Type": "application/json"})
    return [{"time": datetime.fromtimestamp(row["t"] / 1000, UTC), "date": datetime.fromtimestamp(row["t"] / 1000, UTC).date(),
             "open": float(row["o"]), "high": float(row["h"]), "low": float(row["l"]), "close": float(row["c"]),
             "volume": float(row["v"])} for row in rows]
