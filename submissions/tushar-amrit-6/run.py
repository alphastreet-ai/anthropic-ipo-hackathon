#!/usr/bin/env python3
"""Market-implied Monte Carlo for the Anthropic IPO. Standard library only, no API key, no LLM.

  python3 submissions/tushar-amrit-6/run.py             # reproduce prediction.json from the snapshots in data/ (deterministic)
  python3 submissions/tushar-amrit-6/run.py --refresh   # re-fetch Polymarket, Hyperliquid, Google News and FRED, update data/, then predict

Four prices come out of one simulation:
  1. ipo_price   a discrete distribution over the final price. P(above the $70-78 range) pools a base rate from
                 15 hot IPOs, likelihood ratios read from this week's headlines, and Polymarket's proceeds market.
                 The premium above $78 is drawn from the premiums hot comparables achieved, shrunk for deal size,
                 and capped at the 20% Rule 430A limit.
  2-3. day1_open, day1_close   a mixture of three estimators of where ANT trades:
                 Polymarket's day-one market-cap buckets (converted to a per-share price),
                 the per-share pre-IPO perpetual xyz:ANT on Hyperliquid (bias-corrected with the SpaceX case),
                 and a paired bootstrap of 17 large IPOs weighted by deal size, book coverage and recency.
  4. day5_close  the day-one close carried through the comparables' day-5/day-1 ratio, blended with the perp
                 as the anchor for where the stock settles once the first-day pop has passed.
Every number that can be tuned sits in params.json. Every input sits in data/ with its source.
"""
import argparse, bisect, csv, importlib.util, json, math, random, re, statistics, subprocess, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = HERE / "data"
P = json.loads((HERE / "params.json").read_text())
DEAL_DATE = date(2026, 11, 19)  # expected first trade; fixed so a rerun months later gives the same answer
UA = "anthropic-ipo-hackathon-entry/1.0 (public data only)"

PM_SLUGS = [
    "anthropic-ipo-closing-market-cap-higher-strikes", "anthropic-ipo-closing-market-cap-979",
    "anthropic-ipo-closing-market-cap-above", "how-much-will-anthropic-raise-in-its-ipo",
    "what-will-anthropics-ipo-price-per-share-be", "anthropic-opening-share-price-on-ipo-day",
    "anthropic-closing-share-price-on-ipo-day", "anthropic-ipo-closing-price-above-ipo-price-on-first-day",
    "anthropic-ipo-open-price-above-ipo-price", "anthropic-ipo-date",
]
NEWS_QUERIES = ["Anthropic IPO when:3d", "Anthropic IPO oversubscribed when:10d", "Anthropic IPO price range when:7d"]
FRED_SERIES = {"VIXCLS": "VIX", "DGS10": "10-year Treasury yield"}


# ----------------------------------------------------------------------------- fetching (only with --refresh)
def http(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_polymarket():
    events = []
    for slug in PM_SLUGS:
        got = json.loads(http("https://gamma-api.polymarket.com/events?slug=" + slug).decode())
        for ev in got:
            events.append({"slug": ev.get("slug"), "title": ev.get("title"), "volume": ev.get("volume"), "liquidity": ev.get("liquidity"),
                           "description": ev.get("description"),
                           "markets": [{"question": m.get("question"), "outcomes": m.get("outcomes"), "outcomePrices": m.get("outcomePrices"),
                                        "bestBid": m.get("bestBid"), "bestAsk": m.get("bestAsk"), "lastTradePrice": m.get("lastTradePrice"),
                                        "volume": m.get("volume"), "liquidity": m.get("liquidity"), "closed": m.get("closed"),
                                        "updatedAt": m.get("updatedAt")} for m in ev.get("markets") or []]})
    return {"source": "Polymarket Gamma API, https://gamma-api.polymarket.com/events?slug=<slug>", "fetched_at": now_iso(), "events": events}


def fetch_hyperliquid():
    post = lambda body: json.loads(http("https://api.hyperliquid.xyz/info", json.dumps(body).encode(), {"Content-Type": "application/json"}).decode())
    meta, ctxs = post({"type": "metaAndAssetCtxs", "dex": "xyz"})
    ctx = next(c for u, c in zip(meta["universe"], ctxs) if u["name"] == "xyz:ANT")
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    candles = post({"type": "candleSnapshot", "req": {"coin": "xyz:ANT", "interval": "1d", "startTime": end - 60 * 86400000, "endTime": end}})
    return {"source": "Hyperliquid info API, dex xyz, coin xyz:ANT (one Class A share, USD); https://api.hyperliquid.xyz/info",
            "fetched_at": now_iso(), "ctx": ctx, "candles_1d": candles}


def fetch_news():
    """Headline, link, date and source only. Never article text."""
    rows, seen = [], set()
    for i, q in enumerate(NEWS_QUERIES):
        if i:
            time.sleep(1.5)
        feed = http("https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}))
        if b"<!DOCTYPE" in feed or b"<!ENTITY" in feed:
            raise RuntimeError("unexpected DTD in news feed")
        for item in ET.fromstring(feed).iter("item"):
            title = (item.findtext("title") or "").strip()
            key = re.sub(r"\W+", " ", title.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            rows.append({"published": item.findtext("pubDate"), "title": title, "source": (item.findtext("source") or "").strip(),
                         "link": item.findtext("link"), "query": q})
    return {"source": "Google News RSS search", "fetched_at": now_iso(), "headlines": rows}


def fetch_fred():
    rows = []
    for sid, name in FRED_SERIES.items():
        text = http("https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&cosd=2026-08-01" % sid, timeout=60).decode()
        for line in text.splitlines()[1:]:
            d, v = line.split(",")[:2]
            if v not in ("", "."):
                rows.append({"date": d, "series": sid, "name": name, "value": float(v)})
    return {"source": "FRED, https://fred.stlouisfed.org", "fetched_at": now_iso(), "rows": rows}


def fred_from_context():
    """The organisers' daily FRED pull, used when FRED itself is unreachable."""
    f = ROOT / "context/macro/fred.csv"
    rows = [{"date": r["date"], "series": r["series"], "name": r["name"], "value": float(r["value"])}
            for r in csv.DictReader(open(f)) if r["series"] in FRED_SERIES and r["value"] not in ("", ".")]
    return {"source": "context/macro/fred.csv (organisers' FRED pull)", "fetched_at": "see context/README.md", "rows": rows}


def snapshot(name, fetcher, refresh):
    path = DATA / (name + ".json")
    if refresh:
        try:
            data = fetcher()
            path.write_text(json.dumps(data, indent=1) + "\n")
            print("refreshed %s" % path.relative_to(ROOT))
        except Exception as e:  # keep the committed snapshot if a source is down
            print("WARNING: %s refresh failed (%s); using the committed snapshot" % (name, e), file=sys.stderr)
    if not path.exists() and name == "fred":
        data = fred_from_context()
        path.write_text(json.dumps(data, indent=1) + "\n")
    return json.loads(path.read_text())


# ----------------------------------------------------------------------------- deal terms
def load_deal():
    d = dict(P["deal"])
    f = ROOT / "context/anthropic-s1/offering.csv"
    if f.exists():
        offering = {r["item"]: r["value"] for r in csv.DictReader(open(f))}
        d["range_low"] = float(offering.get("price_range_low", d["range_low"]))
        d["range_high"] = float(offering.get("price_range_high", d["range_high"]))
        d["shares_offered"] = float(offering.get("shares_offered", d["shares_offered"]))
        d["shares_outstanding_after_ipo"] = float(offering.get("shares_outstanding_after_ipo", d["shares_outstanding_after_ipo"]))
    d["deal_size_usd_bn"] = d["shares_offered"] * d["range_high"] / 1e9
    return d


# ----------------------------------------------------------------------------- Polymarket
MONEY = re.compile(r"(\$\s*\d+(?:\.\d+)?\s*[TtBbMm]?|\b\d+(?:\.\d+)?\s*[TtBb]\b)")


def parse_bucket(question):
    """'between $1.25T and $1.5T' -> (1.25e12, 1.5e12); 'less than $65B' -> (None, 65e9); '$3.0T or greater' -> (3e12, None)."""
    q = re.sub(r"by\s+\w+\s+\d{1,2},?\s+\d{4}", "", question)  # drop resolution dates
    ql = q.lower()
    if "not ipo" in ql or "no anthropic ipo" in ql or "no ipo" in ql:
        return None
    nums = []
    for m in MONEY.finditer(q):
        tok = m.group(0).replace("$", "").replace(" ", "")
        unit = tok[-1].lower() if tok[-1].isalpha() else ""
        nums.append(float(tok.rstrip("TtBbMm")) * {"t": 1e12, "b": 1e9, "m": 1e6, "": 1.0}[unit])
    if not nums:
        return None
    if "between" in ql and len(nums) >= 2:
        return (min(nums[:2]), max(nums[:2]))
    if any(k in ql for k in ("less than", "below", "under")):
        return (None, nums[0])
    if any(k in ql for k in ("or greater", "or more", "at least", "above", "over", "higher")):
        return (nums[0], None)
    return None


def yes_price(m):
    try:
        return float(json.loads(m["outcomePrices"])[0])
    except Exception:
        return None


def event_cdf_points(ev):
    """Return [(x, F(x))] for one event: a bucket partition or a set of 'above x' thresholds."""
    rows = []
    for m in ev["markets"]:
        b, p = parse_bucket(m["question"]), yes_price(m)
        if b and p is not None:
            rows.append((b, p))
    if not rows:
        return []
    if all(lo is not None and hi is None for (lo, hi), _ in rows):  # cumulative 'above x' markets
        pts = sorted((lo, 1 - p) for (lo, _), p in rows)
    else:  # partition
        total = sum(p for _, p in rows) or 1.0
        rows.sort(key=lambda r: (-1 if r[0][0] is None else r[0][0]))
        cum, pts = 0.0, []
        for (lo, hi), p in rows:
            cum += p / total
            if hi is not None:
                pts.append((hi, cum))
    out, run = [], 0.0
    for x, f in pts:  # a CDF never decreases
        run = max(run, min(1.0, max(0.0, f)))
        out.append((x, run))
    return out


def interp_cdf(pts, x):
    if not pts:
        return None
    xs = [p[0] for p in pts]
    lo_x, hi_x = xs[0] * 0.5, xs[-1] * 1.6  # extrapolate to 0 and 1 beyond the quoted strikes
    if x <= lo_x:
        return 0.0
    if x >= hi_x:
        return 1.0
    ext = [(lo_x, 0.0)] + pts + [(hi_x, 1.0)]
    for (x0, f0), (x1, f1) in zip(ext, ext[1:]):
        if x0 <= x <= x1:
            return f0 if x1 == x0 else f0 + (f1 - f0) * (x - x0) / (x1 - x0)
    return 1.0


class MarketCDF:
    """A per-share CDF for the day-one close, pooled from Polymarket's market-cap events, weighted by log volume."""

    def __init__(self, events, shares):
        caps = [ev for ev in events if "closing-market-cap" in (ev.get("slug") or "")]
        parts = [(event_cdf_points(ev), math.log1p(float(ev.get("volume") or 0))) for ev in caps]
        parts = [(pts, w) for pts, w in parts if pts]
        self.events_used = len(parts)
        grid = [0.4e12 + i * 0.01e12 for i in range(0, 361)]  # $0.4T ... $4.0T
        F, run = [], 0.0
        for x in grid:
            f = sum(w * interp_cdf(pts, x) for pts, w in parts) / sum(w for _, w in parts)
            run = max(run, f)
            F.append(run)
        self.xs = [x / shares for x in grid]
        self.F = F

    def quantile(self, u):
        i = bisect.bisect_left(self.F, u)
        if i <= 0:
            return self.xs[0]
        if i >= len(self.xs):
            return self.xs[-1]
        f0, f1 = self.F[i - 1], self.F[i]
        return self.xs[i] if f1 == f0 else self.xs[i - 1] + (self.xs[i] - self.xs[i - 1]) * (u - f0) / (f1 - f0)

    def sample(self, rng):
        return self.quantile(rng.random())


def polymarket_features(pm, deal):
    ev = {e["slug"]: e for e in pm["events"]}
    out = {"fetched_at": pm.get("fetched_at")}
    # proceeds market -> P(price >= 65e9 / shares offered)
    e = ev.get("how-much-will-anthropic-raise-in-its-ipo")
    if e:
        rows = [(parse_bucket(m["question"]), yes_price(m)) for m in e["markets"]]
        rows = [(b, p) for b, p in rows if b and p is not None]
        total = sum(p for _, p in rows) or 1.0
        below65 = sum(p for (lo, hi), p in rows if hi is not None and hi <= 65e9) / total
        out["p_proceeds_ge_65bn"] = round(1 - below65, 4)
        out["price_threshold_for_65bn"] = round(65e9 / deal["shares_offered"], 2)
        out["proceeds_volume_usd"] = round(float(e.get("volume") or 0))
    # thin per-share IPO price buckets -> P(price > range_high)
    e = ev.get("what-will-anthropics-ipo-price-per-share-be")
    if e:
        rows = [(parse_bucket(m["question"]), yes_price(m)) for m in e["markets"]]
        rows = [(b, p) for b, p in rows if b and p is not None]
        total = sum(p for _, p in rows) or 1.0
        out["p_price_above_top_from_buckets"] = round(sum(p for (lo, hi), p in rows if lo is not None and lo >= deal["range_high"]) / total, 4)
        out["price_buckets_volume_usd"] = round(float(e.get("volume") or 0))
    for slug, key in (("anthropic-ipo-closing-price-above-ipo-price-on-first-day", "p_close_above_ipo_price"),
                      ("anthropic-ipo-open-price-above-ipo-price", "p_open_above_ipo_price")):
        if ev.get(slug) and ev[slug]["markets"]:
            out[key] = yes_price(ev[slug]["markets"][0])
    for slug, key in (("anthropic-opening-share-price-on-ipo-day", "thin_open_market_median"),
                      ("anthropic-closing-share-price-on-ipo-day", "thin_close_market_median")):
        if ev.get(slug):
            pts = event_cdf_points(ev[slug])
            if pts:
                m = MarketCDF.__new__(MarketCDF)
                m.xs, m.F = [x for x, _ in pts], [f for _, f in pts]
                out[key] = round(m.quantile(0.5), 2)
    e = ev.get("anthropic-ipo-date")
    if e:
        for m in e["markets"]:
            if "November 19" in m["question"]:
                out["p_first_trade_nov_19"] = yes_price(m)
    cdf = MarketCDF(pm["events"], deal["shares_outstanding_after_ipo"])
    out["cap_events_used"] = cdf.events_used
    out["cap_market_volume_usd"] = round(sum(float(e.get("volume") or 0) for e in pm["events"] if "closing-market-cap" in (e.get("slug") or "")))
    out["implied_day1_close"] = {"p10": round(cdf.quantile(0.1), 2), "median": round(cdf.quantile(0.5), 2), "p90": round(cdf.quantile(0.9), 2)}
    out["implied_median_market_cap_usd_tn"] = round(cdf.quantile(0.5) * deal["shares_outstanding_after_ipo"] / 1e12, 3)
    return out, cdf


# ----------------------------------------------------------------------------- Hyperliquid perp
def perp_features(hl):
    ctx = hl["ctx"]
    closes = [float(c["c"]) for c in hl["candles_1d"]]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])][-10:]
    rv = statistics.pstdev(rets) if len(rets) >= 3 else 0.05
    return {"fetched_at": hl.get("fetched_at"), "mark": float(ctx["markPx"]), "mid": float(ctx.get("midPx") or ctx["markPx"]),
            "oracle": float(ctx.get("oraclePx") or ctx["markPx"]), "prev_day": float(ctx.get("prevDayPx") or ctx["markPx"]),
            "open_interest_contracts": round(float(ctx.get("openInterest") or 0)), "day_notional_volume_usd": round(float(ctx.get("dayNtlVlm") or 0)),
            "funding_hourly": float(ctx.get("funding") or 0), "daily_closes": closes[-10:], "realized_daily_vol": round(rv, 4),
            "open_interest_usd": round(float(ctx.get("openInterest") or 0) * float(ctx["markPx"]))}


# ----------------------------------------------------------------------------- headlines
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
         "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
COVER = re.compile(r"(\d+(?:\.\d+)?|" + "|".join(WORDS) + r")(?:\s*(?:-|–|to)\s*(\d+(?:\.\d+)?|" + "|".join(WORDS) + r"))?\s*(?:x|times)\s*"
                   r"(?:over-?subscri\w+|oversubscription|demand|covered|coverage|the\s+(?:shares|stock|deal|offering|book)|available)", re.I)
PATTERNS = {
    "above": re.compile(r"(?:pric\w*|ipo|shares?|deal)\s+(?:its\s+ipo\s+)?above\s+(?:the\s+)?(?:current\s+|marketed\s+|proposed\s+)?(?:price\s+)?range"
                        r"|above\s+(?:the\s+)?(?:current\s+|marketed\s+)?(?:price\s+)?range|above\s+(?:its\s+)?(?:target(?:ed)?|marketed)\s+range", re.I),
    "top_or_above": re.compile(r"top\s+of\s+(?:the\s+|its\s+)?(?:price\s+)?range\s+or\s+(?:above|higher|more)|\$?78\s+or\s+(?:more|above|higher)", re.I),
    "top_only": re.compile(r"top\s+(?:end\s+)?of\s+(?:the\s+|its\s+)?(?:price\s+)?range|high\s+end\s+of\s+(?:the\s+|its\s+)?range", re.I),
    "hold_range": re.compile(r"(?:stick\w*|hold\w*|maintain\w*|keep\w*|reaffirm\w*)\s+(?:to\s+|with\s+)?(?:its\s+)?(?:proposed\s+|current\s+)?(?:ipo\s+)?(?:price\s+)?range", re.I),
    "raise_range": re.compile(r"(?:rais\w*|boost\w*|lift\w*|hik\w*|increas\w*)\s+(?:its\s+)?(?:ipo\s+)?(?:price\s+)?range|range\s+(?:raised|lifted|increased)", re.I),
    "below_or_cut": re.compile(r"below\s+(?:the\s+)?(?:its\s+)?(?:price\s+)?range|(?:cut\w*|lower\w*|slash\w*|trim\w*)\s+(?:its\s+)?(?:ipo\s+)?(?:price\s+)?range"
                               r"|downsiz\w*|(?:delay\w*|postpon\w*|pull\w*|shelv\w*)\s+(?:its\s+)?ipo", re.I),
}


def tier(source):
    s = (source or "").lower()
    return 1 if any(t in s for t in P["news"]["tier1"]) else 2


def to_num(tok):
    return WORDS.get(tok.lower()) or float(tok)


def news_features(news):
    cover, hits, lr = [], {k: [] for k in PATTERNS}, 1.0
    for h in news["headlines"]:
        t = h["title"] or ""
        for m in COVER.finditer(t):
            a, b = to_num(m.group(1)), (to_num(m.group(2)) if m.group(2) else None)
            v = (a + b) / 2 if b else a
            if 1 <= v <= 100:
                cover.append({"x": v, "title": t, "source": h["source"], "published": h["published"]})
        kind = None
        if PATTERNS["top_or_above"].search(t):
            kind = "top_or_above"
        elif PATTERNS["above"].search(t) and not PATTERNS["below_or_cut"].search(t):
            kind = "above"
        elif PATTERNS["below_or_cut"].search(t):
            kind = "below_or_cut"
        elif PATTERNS["raise_range"].search(t):
            kind = "raise_range"
        elif PATTERNS["hold_range"].search(t):
            kind = "hold_range"
        elif PATTERNS["top_only"].search(t):
            kind = "top_only"
        if kind:
            hits[kind].append({"title": t, "source": h["source"], "tier": tier(h["source"]), "published": h["published"]})
    # syndicated copies of one wire story are not independent evidence: each signal counts once, at its best tier
    for kind, hs in hits.items():
        if hs:
            lr *= P["ipo_price"]["news_lr"]["%s_tier%d" % (kind, min(h["tier"] for h in hs))]
    lr = max(1 / P["ipo_price"]["news_lr"]["cap"], min(P["ipo_price"]["news_lr"]["cap"], lr))
    xs = [c["x"] for c in cover]
    return {"fetched_at": news.get("fetched_at"), "headlines": len(news["headlines"]), "coverage_mentions": cover,
            "coverage_x": statistics.median(xs) if xs else P["news"]["default_coverage_x"], "pricing_hits": hits, "news_lr_above": round(lr, 3)}


# ----------------------------------------------------------------------------- comparables
def load_comps(deal, cov_ant):
    S = P["trading"]["similarity"]
    rows = []
    for r in csv.DictReader(open(DATA / "comps.csv")):
        ipo, top = float(r["ipo_price"]), float(r["range_high"])
        o, c, d5 = float(r["day1_open"]), float(r["day1_close"]), float(r["day5_close"])
        size, cov = float(r["raised_usd_bn"]), (float(r["coverage_x"]) if r["coverage_x"] else None)
        years = (DEAL_DATE - date.fromisoformat(r["ipo_date"])).days / 365.25
        hot = ipo >= top
        w = math.exp(-abs(math.log(size / deal["deal_size_usd_bn"])) / S["size_bandwidth"])
        w *= math.exp(-abs(math.log(cov / cov_ant)) / S["coverage_bandwidth"]) if cov else S["unknown_coverage_factor"]
        w *= 0.5 ** (years / S["recency_half_life_years"])
        w *= 1.0 if hot else S["not_hot_factor"]
        rows.append({"company": r["company"], "ticker": r["ticker"], "hot": hot, "premium": ipo / top - 1, "r_open": o / ipo, "r_close": c / ipo,
                     "r_fade": d5 / c, "r_open_close": o / c, "size_bn": size, "coverage_x": cov, "years_ago": round(years, 1), "weight": w})
    # bigger deals pop less: fit log(day-1 close / IPO price) on log(deal size) across the hot comps and move every
    # comparable's first-day ratios to Anthropic's deal size along that slope (clipped so one outlier cannot run away)
    hot = [r for r in rows if r["hot"]]
    xs, ys = [math.log(r["size_bn"]) for r in hot], [math.log(r["r_close"]) for r in hot]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    beta = max(S.get("size_slope_min", -0.35), min(0.0, beta))
    for r in rows:
        adj = math.exp(beta * (math.log(deal["deal_size_usd_bn"]) - math.log(r["size_bn"])))
        r["r_open_raw"], r["r_close_raw"] = r["r_open"], r["r_close"]
        r["r_open"], r["r_close"] = 1 + (r["r_open"] - 1) * adj if r["r_open"] > 1 else r["r_open"], 1 + (r["r_close"] - 1) * adj if r["r_close"] > 1 else r["r_close"]
        r["r_open_close"] = r["r_open"] / r["r_close"]
        r["size_slope"] = round(beta, 4)
    cap = S.get("max_weight", 1.0)
    for _ in range(50):  # normalise, then cap any single deal and redistribute, until stable
        tot = sum(r["weight"] for r in rows)
        for r in rows:
            r["weight"] = r["weight"] / tot
        over = [r for r in rows if r["weight"] > cap + 1e-9]
        if not over:
            break
        excess = sum(r["weight"] - cap for r in over)
        under_tot = sum(r["weight"] for r in rows if r["weight"] <= cap)
        for r in rows:
            r["weight"] = cap if r in over else r["weight"] * (1 + excess / under_tot)
    return rows


def wquantile(pairs, q):
    pairs = sorted(pairs)
    tot, run = sum(w for _, w in pairs), 0.0
    for x, w in pairs:
        run += w
        if run >= q * tot:
            return x
    return pairs[-1][0]


# ----------------------------------------------------------------------------- 1. the IPO price
def logit(p):
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def ipo_price_distribution(deal, comps, news, pm):
    C = P["ipo_price"]
    hot = [c for c in comps if c["hot"]]
    prior = sum(1 for c in hot if c["premium"] > 0) / len(hot)
    p_news = 1 / (1 + math.exp(-(logit(prior) + math.log(news["news_lr_above"]))))
    pooled, wsum = C["pool_weights"]["comps_prior_x_news"] * logit(p_news), C["pool_weights"]["comps_prior_x_news"]
    if "p_proceeds_ge_65bn" in pm:
        pooled += C["pool_weights"]["polymarket_proceeds"] * logit(pm["p_proceeds_ge_65bn"])
        wsum += C["pool_weights"]["polymarket_proceeds"]
    if "p_price_above_top_from_buckets" in pm:
        pooled += C["pool_weights"]["polymarket_price_buckets"] * logit(pm["p_price_above_top_from_buckets"])
        wsum += C["pool_weights"]["polymarket_price_buckets"]
    p_above = 1 / (1 + math.exp(-pooled / wsum))
    p_below, p_within = C["p_below_range"], C["p_within_below_top"]
    p_above = min(p_above, 1 - p_below - p_within - 0.02)
    p_top = 1 - p_above - p_below - p_within
    top, step = deal["range_high"], C["grid_step"]
    prems = [c["premium"] * C["premium_shrink"] for c in hot if c["premium"] > 0]
    grid = []
    # above the top: a kernel density over the shrunk premiums, capped at the Rule 430A limit
    above = []
    x = top + step
    while x <= top * (1 + deal["rule_430a_max_premium"]) + 1e-9:
        prem = x / top - 1
        above.append((x, sum(math.exp(-0.5 * ((prem - m) / C["premium_kernel_sigma"]) ** 2) for m in prems)))
        x += step
    tot = sum(m for _, m in above)
    grid += [(x, p_above * m / tot) for x, m in above]
    grid.append((top, p_top))
    within = [deal["range_low"] + i * step for i in range(int((top - deal["range_low"]) / step))]
    grid += [(x, p_within / len(within)) for x in within]
    below = [deal["range_low"] - 8 + i * step for i in range(int(8 / step))]
    grid += [(x, p_below / len(below)) for x in below]
    grid.sort()
    return {"prior_above_from_hot_comps": round(prior, 3), "hot_comps": len(hot), "news_lr_above": news["news_lr_above"],
            "p_above_after_news": round(p_news, 3), "p_above_pooled": round(p_above, 3), "p_top_exactly": round(p_top, 3),
            "p_within_below_top": p_within, "p_below_range": p_below, "premiums_used": [round(p, 4) for p in prems],
            "quantiles": {"p10": wquantile(grid, 0.1), "median": wquantile(grid, 0.5), "p90": wquantile(grid, 0.9)}}, grid


# ----------------------------------------------------------------------------- 2-4. where it trades
def simulate(deal, grid, comps, perp, cdf, vix_scale, rng):
    T = P["trading"]
    n = int(P["sims"])
    ipo_x, ipo_w = [x for x, _ in grid], [w for _, w in grid]
    c_w = [c["weight"] for c in comps]
    comp_names = ["polymarket", "perp", "comps"]
    comp_w = [T["mixture"][k] for k in comp_names]
    s_perp = math.sqrt(2 * perp["realized_daily_vol"] ** 2 + T["perp_basis_sigma"] ** 2) * vix_scale
    s_perp5 = math.sqrt(7 * perp["realized_daily_vol"] ** 2 + T["perp_basis_sigma"] ** 2) * vix_scale
    jit = T["comps_jitter_sigma"] * vix_scale
    dif_w, dif_s = T["diffuse"]["weight"], T["diffuse"]["sigma"]
    lo, hi = T["price_floor"], T["price_cap"]
    ipo_s = rng.choices(ipo_x, ipo_w, k=n)
    comp_s = rng.choices(comps, c_w, k=n)
    which = rng.choices(comp_names, comp_w, k=n)
    out = {"ipo_price": ipo_s, "day1_open": [], "day1_close": [], "day5_close": []}
    g = rng.gauss
    for i in range(n):
        ipo, c, w = ipo_s[i], comp_s[i], which[i]
        if w == "polymarket":
            close = cdf.sample(rng)
            opn = close * c["r_open_close"] * math.exp(g(0, jit))
        elif w == "perp":
            close = perp["mark"] * (1 + T["perp_bias"]["close"]) * math.exp(g(0, s_perp))
            opn = close * (1 + T["perp_bias"]["open"]) / (1 + T["perp_bias"]["close"]) * math.exp(g(0, 0.06 * vix_scale))
        else:
            close = ipo * c["r_close"] * math.exp(g(0, jit))
            opn = ipo * c["r_open"] * math.exp(g(0, jit))
        if rng.random() < T["day5_perp_weight"]:
            d5 = perp["mark"] * (1 + T["perp_bias"]["day5"]) * math.exp(g(0, s_perp5))
        else:
            d5 = close * c["r_fade"] * math.exp(g(0, jit))
        if rng.random() < dif_w:
            opn *= math.exp(g(0, dif_s))
        if rng.random() < dif_w:
            close *= math.exp(g(0, dif_s))
        if rng.random() < dif_w:
            d5 *= math.exp(g(0, dif_s))
        out["day1_open"].append(min(hi, max(lo, opn)))
        out["day1_close"].append(min(hi, max(lo, close)))
        out["day5_close"].append(min(hi, max(lo, d5)))
    return out


def quantiles(xs):
    s = sorted(xs)
    pick = lambda q: s[min(len(s) - 1, int(q * len(s)))]
    return {"median": round(pick(0.5), 2), "p10": round(pick(0.1), 2), "p90": round(pick(0.9), 2)}


# ----------------------------------------------------------------------------- words
def thesis_and_risks(deal, ipo, pm, perp, news, comps, q, comps_only):
    top_names = ", ".join(c["company"] for c in sorted(comps, key=lambda c: -c["weight"])[:6])
    tier1_above = [h for k in ("above", "top_or_above") for h in news["pricing_hits"][k] if h["tier"] == 1]
    outlets = ", ".join(sorted({h["source"] for h in tier1_above})) or "no tier-1 outlet"
    thesis = (
        "Pricing: the book is reported at about {cov:.0f}x the ${size:.0f}B on offer ({ncov} headline mentions), and {outlets} report guidance to the top of the "
        "$70-78 range or above. Pooling a {prior:.0%} base rate of above-range pricing from {nhot} hot IPOs with those headlines (likelihood ratio {lr:.1f}) and "
        "Polymarket's proceeds market (P(>=$65B) = {ppm:.0%}, i.e. a price above ${thr:.0f}) gives P(above $78) = {pab:.0%}. The premium above the top is drawn from "
        "what hot deals achieved (3-16% of the range top), shrunk by {shrink:.0%} because the four largest comparables (Facebook, Alibaba, Arm, SpaceX) all stopped at "
        "the top and the $26B placement partners pay the IPO price, and capped at the 20% Rule 430A limit: median ${ipo_m:.2f}, 80% range ${ipo_lo:.2f}-${ipo_hi:.2f}. "
        "Trading: we read where ANT trades from money-backed markets rather than from the pops of small deals. Hyperliquid's per-share pre-IPO perpetual xyz:ANT marks "
        "${perp:.2f} ({perp_vs:+.0%} vs $78) on ${oi:.0f}M of open interest; Polymarket's ${pmvol:.1f}M day-one market-cap market implies a median close of ${pm_m:.0f} on "
        "{shares:.2f}B shares (median cap ${cap:.2f}T); and a paired bootstrap of 17 IPOs weighted by deal size, book coverage and recency ({top_names} carry most weight), with each "
        "deal's first-day move scaled to a ${size:.0f}B deal along the fitted size slope ({beta:.2f} per log-unit of size), puts the close at {rc:.2f}x the IPO "
        "price (${co_m:.0f}). The three agree within a few dollars, so the mixture gives open ${op_m:.2f} and close ${cl_m:.2f}. "
        "The perp is bias-corrected by half of its only precedent (it sat 17% above SpaceX's open and 9% above its first close). Day five carries the close through the "
        "comparables' day-5/day-1 ratio (weighted median {fade:.2f}x) and blends it with the perp as the post-pop anchor: ${d5_m:.2f}. Ranges are mixture quantiles with a "
        "diffuse component for model error, and they are deliberately wide: Nvidia reports between this run and pricing, and a 20% retail slice plus up to 316M "
        "shares released from lockup on day one make the supply picture unlike past hot IPOs."
    ).format(cov=news["coverage_x"], size=deal["deal_size_usd_bn"], ncov=len(news["coverage_mentions"]), outlets=outlets, prior=ipo["prior_above_from_hot_comps"],
             nhot=ipo["hot_comps"], lr=ipo["news_lr_above"], ppm=pm.get("p_proceeds_ge_65bn", 0), thr=pm.get("price_threshold_for_65bn", 0),
             pab=ipo["p_above_pooled"], shrink=1 - P["ipo_price"]["premium_shrink"], ipo_m=q["ipo_price"]["median"], ipo_lo=q["ipo_price"]["p10"],
             ipo_hi=q["ipo_price"]["p90"], perp=perp["mark"], perp_vs=perp["mark"] / deal["range_high"] - 1, oi=perp["open_interest_usd"] / 1e6,
             pmvol=pm["cap_market_volume_usd"] / 1e6, pm_m=pm["implied_day1_close"]["median"], shares=deal["shares_outstanding_after_ipo"] / 1e9,
             cap=pm["implied_median_market_cap_usd_tn"], top_names=top_names, rc=comps_only["r_close_median"], co_m=comps_only["close_median"],
             beta=comps[0]["size_slope"],
             op_m=q["day1_open"]["median"], cl_m=q["day1_close"]["median"], fade=comps_only["r_fade_median"], d5_m=q["day5_close"]["median"])
    risks = [
        "Nvidia reports after the close on 17 Nov, hours before pricing. The last AI selloff (5-6 Nov) took the xyz:ANT perp down about 20% in two days; a repeat would "
        "drag the IPO price toward $78 and day-one trading below our medians.",
        "Supply on day one is unusual: up to 20% of the deal goes to retail and up to 316M shares are released from lockup on the first day, so the open may not clear "
        "as far above the IPO price as past hot deals with tiny floats.",
        "A range amendment on the morning of 18 Nov, or pricing more than 20% above $78 (the Rule 430A limit without refiling), would put ipo_price above our p90.",
        "Pre-IPO perps overshot SpaceX's open by about 17% and its IPO price by 22%; if xyz:ANT carries the same premium, the first trade lands nearer $85 than $95 and "
        "the perp leg of our mixture is too high.",
        "Polymarket's market-cap buckets become per-share prices through the 13.09B post-IPO share count in the S-1/A; if traders are anchoring on the ~18B fully "
        "diluted count instead, the market-implied close is lower than we read it.",
    ]
    return thesis, risks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(HERE), help="folder for prediction.json and run_log.json (default: this folder)")
    ap.add_argument("--name", default="tushar-amrit-6/market-implied-monte-carlo")
    ap.add_argument("--repo", default="https://github.com/tushar-amrit-6/anthropic-ipo-hackathon/tree/main/submissions/tushar-amrit-6")
    ap.add_argument("--refresh", action="store_true", help="re-fetch the public sources and overwrite data/*.json before predicting")
    ap.add_argument("--seed", type=int, default=P["seed"])
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    deal = load_deal()
    pm_raw = snapshot("polymarket", fetch_polymarket, args.refresh)
    hl_raw = snapshot("hyperliquid", fetch_hyperliquid, args.refresh)
    news_raw = snapshot("headlines", fetch_news, args.refresh)
    fred_raw = snapshot("fred", fetch_fred, args.refresh)

    news = news_features(news_raw)
    pm, cdf = polymarket_features(pm_raw, deal)
    perp = perp_features(hl_raw)
    vix_rows = [r for r in fred_raw["rows"] if r["series"] == "VIXCLS"]
    vix = vix_rows[-1]["value"] if vix_rows else 20.0
    vix_scale = min(1.6, max(0.8, vix / 20.0))
    comps = load_comps(deal, news["coverage_x"])
    ipo, grid = ipo_price_distribution(deal, comps, news, pm)

    ipo_m = ipo["quantiles"]["median"]
    comps_only = {"r_open_median": round(wquantile([(c["r_open"], c["weight"]) for c in comps], 0.5), 3),
                  "r_close_median": round(wquantile([(c["r_close"], c["weight"]) for c in comps], 0.5), 3),
                  "r_fade_median": round(wquantile([(c["r_fade"], c["weight"]) for c in comps], 0.5), 3)}
    comps_only["open_median"] = round(ipo_m * comps_only["r_open_median"], 2)
    comps_only["close_median"] = round(ipo_m * comps_only["r_close_median"], 2)

    rng = random.Random(args.seed)
    sims = simulate(deal, grid, comps, perp, cdf, vix_scale, rng)
    q = {k: quantiles(v) for k, v in sims.items()}
    q["ipo_price"] = {k: round(v, 2) for k, v in ipo["quantiles"].items()}

    thesis, risks = thesis_and_risks(deal, ipo, pm, perp, news, comps, q, comps_only)
    rel_width = statistics.mean((q[k]["p90"] - q[k]["p10"]) / q[k]["median"] for k in q)
    confidence = round(min(0.7, max(0.2, 0.75 - 0.5 * rel_width)), 2)
    assert 50 <= len(thesis) <= 2500, len(thesis)
    assert 3 <= len(risks) <= 5 and all(5 <= len(r) <= 300 for r in risks)

    prediction = {"name": args.name,
                  "method": "Market-implied Monte Carlo: Polymarket cap buckets + Hyperliquid xyz:ANT pre-IPO perp + size/coverage-weighted IPO comps + headline-mined book coverage; stdlib Python, no LLM",
                  "repo_url": args.repo, **{k: q[k] for k in ("ipo_price", "day1_open", "day1_close", "day5_close")},
                  "thesis": thesis, "key_risks": risks, "confidence": confidence}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prediction.json").write_text(json.dumps(prediction, indent=2) + "\n")
    log = {"run_at": now_iso(), "seed": args.seed, "sims": P["sims"], "deal": deal, "vix": vix, "vix_scale": round(vix_scale, 3),
           "news": {k: v for k, v in news.items() if k != "coverage_mentions"}, "coverage_mentions": news["coverage_mentions"],
           "polymarket": pm, "perp": perp, "comps": comps, "comps_only": comps_only, "ipo_price_model": ipo, "quantiles": q,
           "confidence": confidence, "params": P}
    (out / "run_log.json").write_text(json.dumps(log, indent=1, default=str) + "\n")

    print("\n== inputs ==")
    print("book coverage (headline median): %.1fx from %d mentions" % (news["coverage_x"], len(news["coverage_mentions"])))
    for k, hs in news["pricing_hits"].items():
        for h in hs:
            print("  headline[%s, tier %d] %s (%s)" % (k, h["tier"], h["title"][:90], h["source"]))
    print("Polymarket: P(proceeds>=65B)=%.2f  P(above $78, thin buckets)=%.2f  implied day-1 close %s  (cap market vol $%.1fM)" % (
        pm.get("p_proceeds_ge_65bn", float("nan")), pm.get("p_price_above_top_from_buckets", float("nan")), pm["implied_day1_close"], pm["cap_market_volume_usd"] / 1e6))
    print("Hyperliquid xyz:ANT: mark $%.2f  realized daily vol %.1f%%  OI $%.0fM" % (perp["mark"], 100 * perp["realized_daily_vol"], perp["open_interest_usd"] / 1e6))
    print("VIX %.1f -> vol scale %.2f" % (vix, vix_scale))
    print("comps weights:", ", ".join("%s %.2f" % (c["ticker"], c["weight"]) for c in sorted(comps, key=lambda c: -c["weight"])[:8]))
    print("comps-only medians (size-adjusted, slope %.2f): open x%.2f close x%.2f day5/day1 x%.2f" % (
        comps[0]["size_slope"], comps_only["r_open_median"], comps_only["r_close_median"], comps_only["r_fade_median"]))
    print("\n== ipo_price model ==")
    print("prior(above|hot)=%.2f  news LR=%.2f  pooled P(above)=%.2f  P(top)=%.2f  P(within)=%.2f  P(below)=%.2f" % (
        ipo["prior_above_from_hot_comps"], ipo["news_lr_above"], ipo["p_above_pooled"], ipo["p_top_exactly"], ipo["p_within_below_top"], ipo["p_below_range"]))
    print("\n== prediction ==")
    for k in ("ipo_price", "day1_open", "day1_close", "day5_close"):
        print("%-11s median %7.2f   p10 %7.2f   p90 %7.2f" % (k, q[k]["median"], q[k]["p10"], q[k]["p90"]))
    print("confidence %.2f" % confidence)
    print("wrote %s and run_log.json" % (out / "prediction.json"))
    if not args.no_validate and (ROOT / "scripts/validate.py").exists():
        if importlib.util.find_spec("jsonschema") is None:
            print("jsonschema is not installed, so scripts/validate.py was skipped here (CI runs it; `pip install jsonschema` to run it locally)")
        else:
            sys.exit(subprocess.run([sys.executable, str(ROOT / "scripts/validate.py"), str(out / "prediction.json")]).returncode)


if __name__ == "__main__":
    main()
