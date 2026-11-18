#!/usr/bin/env python3
"""Market-triangulation forecaster for the Anthropic (ANT) IPO.

  python3 submissions/poulami95/forecast.py            # rebuilds prediction.json, report.html and run_log.txt

Standard library only, no API key, fixed seed: the same inputs give the same file.

Idea: three money-backed views of ANT already exist before the first trade, each strongest for one horizon.
  1. The order book (guidance, oversubscription)   -> the IPO price.
  2. Hyperliquid's xyz:ANT pre-IPO perpetual        -> an absolute anchor for the day-1 open.
  3. Polymarket's first-day closing market-cap bets -> a full distribution for the day-1 close.
A Monte Carlo simulates joint paths (IPO -> open -> close -> day 5) from the book and comparable IPOs,
then exponentially tilts the paths toward the Polymarket brackets, so every horizon stays consistent with
the market's own probabilities. Day 5 adds comparable-IPO drift, the first-day lock-up release and
greenshoe support. VIX scales volatility; the AlphaStreet signal pack (if present) adds a bounded tilt.
"""
import argparse, csv, json, math, random, statistics, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
KEYS = ["ipo_price", "day1_open", "day1_close", "day5_close"]

# Every tunable number, with the reason for its value. Change here, rerun, commit.
CFG = {
    "n_paths": 200_000,
    "seed": 20261118,
    # IPO price: guided "top of range or above" (Bloomberg, 17 Nov), book ~7x, and on 18 Nov the WSJ reported
    # a plan to raise the price, possibly above $80. So pricing at or under $78 is now the tail, not the base case.
    "p_below_top": 0.02,        # residual chance the book cracks and it prices under $78
    "p_at_top": 0.15,           # prices exactly at $78 without lifting the range
    "raise_median": 0.07,       # if above: median raise over $78 (~$83.5); hot mega-deals usually lift 5-10%
    "raise_sigma": 0.55,        # log-spread of the raise
    "min_pop_vs_perp": 0.08,    # banks leave at least ~8% headroom below the grey-market (perp) price
    # Day-1 open: two legs, blended in log space.
    "w_perp": 0.60,             # weight on the perp anchor vs the comparable-IPO pop leg
    "perp_basis": -0.03,        # offshore retail perp tends to trade rich; it fell from $138 to ~$97 in three weeks
    "perp_sd": 0.08,            # perp drift over ~1.5 days plus basis uncertainty
    "pop_mu": math.log(1.13),   # SpaceX opened +11% on a ~4x book; ANT's ~7x book argues slightly more
    "pop_sd": 0.10,
    "first_trade_shock": True,  # add a common shock to the open with the spread of comps' first-trade pops
                                # (open / IPO price): averaging two anchors cancels their noise, but real first
                                # trades still surprise (Alibaba and Rivian opened ~+36%, Uber -7%)
    "open_floor": 0.97,         # a 7x-covered deal with underwriter support rarely opens >3% below issue
    "nvidia_beta": 0.5,         # ANT open moves ~half of Nvidia's post-earnings move (supplier + placement partner)
    # Day-1 close and day 5: centre and spread come from comps.csv (median and MAD of log ratios).
    "lockup_drag_close": -0.01, # up to 316M shares may sell on day 1, ~38% of the 825M offered
    "lockup_drag_day5": -0.02,
    "greenshoe_support": 0.40,  # share of any shortfall below the IPO price the stabilisation agent absorbs
    # Polymarket tilt: paths re-weighted by (market prob / model prob)^lambda per close bracket.
    "polymarket_lambda": 0.60,  # 1 = adopt the market fully, 0 = ignore it
    # Calibration: the score doubles the error when the truth falls outside p10..p90.
    "range_inflation": 1.15,    # widen log-distance of p10/p90 from the median by 15%, since crowd and perp
                                # prices are known to be under-dispersed before a first trade
    "vix_neutral": 18.0,        # VIX level at which volatility is left as-is
    "max_signal_tilt": 0.03,    # signal pack can move the log-mean of the open by at most +/-3%
}

LOG = []


def log(msg=""):
    print(msg)
    LOG.append(msg)


# ---------- inputs ----------

def load_evidence():
    return json.loads((HERE / "data/evidence.json").read_text())


def comps_stats():
    rows = list(csv.DictReader(open(HERE / "data/comps.csv")))
    co = [math.log(float(r["day1_close"]) / float(r["day1_open"])) for r in rows]
    pop = [math.log(float(r["day1_open"]) / float(r["ipo_price"])) for r in rows]
    d5 = [math.log(float(r["day5_close"]) / float(r["day1_close"])) for r in rows]

    def robust(xs):
        m = statistics.median(xs)
        return m, 1.4826 * statistics.median(abs(x - m) for x in xs)

    (co_mu, co_sd), (d5_mu, d5_sd), (_, pop_sd) = robust(co), robust(d5), robust(pop)
    return {"n": len(rows), "co_mu": co_mu, "co_sd": co_sd, "d5_mu": d5_mu, "d5_sd": d5_sd, "pop_sd": pop_sd,
            "names": ", ".join(r["company"] for r in rows)}


def vix_multiplier():
    path = ROOT / "context/macro/fred.csv"
    vix = [(r[0], float(r[3])) for r in csv.reader(open(path)) if len(r) > 3 and r[1] == "VIXCLS"]
    date, level = max(vix)
    mult = min(1.5, max(0.85, level / CFG["vix_neutral"]))
    return level, date, mult


def signal_tilt(as_of: datetime):
    """Bounded sentiment tilt from the AlphaStreet pack, point-in-time. Returns (tilt, note)."""
    pack = ROOT / "signals/pack"
    sf, imf = pack / "signals.v1.jsonl", pack / "signal_impacts.v1.jsonl"
    if not (sf.exists() and imf.exists()):
        return 0.0, "signal pack not present (licensed, gitignored, sent to registrants); tilt = 0"
    ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    sigs = {}
    for line in open(sf):
        s = json.loads(line)
        t = ts(s.get("detected_at") or s.get("published_at"))
        if s.get("signal_detection") != "noise" and t and t <= as_of:
            sigs[s["signal_id"]] = (s, t)
    impacts = [json.loads(l) for l in open(imf)]
    own_dir = {(i["signal_id"], i["issuer_id"]): i.get("direction") for i in impacts}
    # good news for a rival is bad for ANT; for suppliers, customers, peers and macro it is read as good
    sign = {"competitor": -1}
    best = {}
    for imp in impacts:
        if imp["issuer_id"] != "ANT" or imp["signal_id"] not in sigs:
            continue
        s, t = sigs[imp["signal_id"]]
        d = s.get("event_direction") or own_dir.get((s["signal_id"], s.get("subject_issuer_id")))
        if not d:
            continue
        age = (as_of - t).total_seconds() / 86400
        w = ((s.get("strength") or 0.5) * (s.get("confidence") or 0.5) * (s.get("source_reliability") or 0.6)
             * (imp.get("link_confidence") or 0.3) * 0.5 ** (age / (s.get("half_life_days") or 4)))
        key = (s.get("cluster_id") or s["signal_id"], imp["link_type"])  # a story counts once per link
        if w > best.get(key, (0, 0))[0]:
            best[key] = (w, d * sign.get(imp["link_type"], 1))
    den = sum(w for w, _ in best.values())
    if not den:
        return 0.0, "signal pack present but no directional ANT rows before as_of; tilt = 0"
    score = sum(w * x for w, x in best.values()) / den
    tilt = CFG["max_signal_tilt"] * math.tanh(2 * score)
    return tilt, f"signal pack: {len(best)} directional story-links, net score {score:+.3f} -> open tilt {tilt:+.2%}"


# ---------- model ----------

def t_draw(rng, df):
    """Student-t scaled to unit standard deviation (fat tails, same spread)."""
    z = rng.gauss(0, 1) / math.sqrt(rng.gammavariate(df / 2, 2) / df)
    return z * math.sqrt((df - 2) / df)


def simulate(ev, cs, vm, tilt):
    c, rng = CFG, random.Random(CFG["seed"])
    hi = ev["offering"]["range_high"]
    perp = ev["perp"]["price"]
    nv = (ev["nvidia"].get("after_hours_move_pct") or 0) / 100
    cap = max(hi, perp / (1 + c["min_pop_vs_perp"]))
    paths = []
    for _ in range(c["n_paths"]):
        u = rng.random()
        if u < c["p_below_top"]:
            ipo = rng.uniform(hi - 4, hi)
        elif u < c["p_below_top"] + c["p_at_top"]:
            ipo = hi
        else:
            ipo = min(hi * (1 + c["raise_median"] * math.exp(c["raise_sigma"] * rng.gauss(0, 1))), cap)
        perp_leg = math.log(perp) + c["perp_basis"] + c["perp_sd"] * vm * t_draw(rng, 4)
        pop_leg = math.log(ipo) + c["pop_mu"] + c["pop_sd"] * vm * rng.gauss(0, 1)
        lo = c["w_perp"] * perp_leg + (1 - c["w_perp"]) * pop_leg + tilt + c["nvidia_beta"] * nv
        if c["first_trade_shock"]:
            lo += cs["pop_sd"] * vm * t_draw(rng, 3)
        opn = max(math.exp(lo), ipo * c["open_floor"])
        close = opn * math.exp(cs["co_mu"] + c["lockup_drag_close"] + cs["co_sd"] * vm * t_draw(rng, 4))
        d5 = close * math.exp(cs["d5_mu"] + c["lockup_drag_day5"] + cs["d5_sd"] * vm * t_draw(rng, 4))
        if d5 < ipo:
            d5 = ipo - (1 - c["greenshoe_support"]) * (ipo - d5)
        paths.append((ipo, opn, close, d5))
    return paths


def polymarket_weights(paths, ev):
    """Exponential tilt of the simulated paths toward Polymarket's first-day close brackets."""
    pm, shares = ev["polymarket_close_mcap"], ev["offering"]["shares_outstanding_after_ipo"]
    br = [(b["lo"] * 1e12 / shares, (b["hi"] or 1e9) * 1e12 / shares, b["prob"]) for b in pm["brackets_usd_tn"]]
    total = sum(p for _, _, p in br)
    q = [p / total for _, _, p in br]
    # paths outside every listed bracket (if a live pull returns a partial ladder) keep weight 1
    idx = [next((i for i, (lo, hi, _) in enumerate(br) if lo <= p[2] < hi), -1) for p in paths]
    n = len(paths)
    model = [idx.count(i) / n for i in range(len(br))]
    covered = sum(model)
    lam = CFG["polymarket_lambda"]
    ratio = [(q[i] * covered / model[i]) ** lam if model[i] > 0 else 0 for i in range(len(br))]
    w = [ratio[i] if i >= 0 else 1.0 for i in idx]
    s = sum(w)
    return [x / s for x in w], br, q, model


def wq(values, weights, qs):
    pairs = sorted(zip(values, weights))
    out, cum, j = [], 0.0, 0
    for q in qs:
        while j < len(pairs) - 1 and cum + pairs[j][1] < q:
            cum += pairs[j][1]
            j += 1
        out.append(pairs[j][0])
    return out


def summarise(paths, w):
    res = {}
    for k, key in enumerate(KEYS):
        p10, med, p90 = wq([p[k] for p in paths], w, [0.10, 0.50, 0.90])
        f = CFG["range_inflation"]
        res[key] = {"median": round(med, 2), "p10": round(med * (p10 / med) ** f, 2), "p90": round(med * (p90 / med) ** f, 2)}
    return res


def prob(paths, w, fn):
    return sum(wi for p, wi in zip(paths, w) if fn(p))


# ---------- outputs ----------

def report_html(res, ev, br, path):
    """Self-contained fan chart: four 80% ranges against the evidence."""
    W, H, L, R = 760, 300, 120, 30
    lo_v, hi_v = 60, max(v["p90"] for v in res.values()) * 1.05
    x = lambda v: L + (v - lo_v) / (hi_v - lo_v) * (W - L - R)
    rows = []
    labels = {"ipo_price": "IPO price", "day1_open": "Day-1 open", "day1_close": "Day-1 close", "day5_close": "Day-5 close"}
    for i, k in enumerate(KEYS):
        y, v = 50 + i * 55, res[k]
        rows.append(f'<text x="{L-12}" y="{y+5}" text-anchor="end" class="l">{labels[k]}</text>'
                    f'<rect x="{x(v["p10"]):.1f}" y="{y-10}" width="{x(v["p90"])-x(v["p10"]):.1f}" height="20" rx="4" class="band"/>'
                    f'<line x1="{x(v["median"]):.1f}" x2="{x(v["median"]):.1f}" y1="{y-14}" y2="{y+14}" class="med"/>'
                    f'<text x="{x(v["median"]):.1f}" y="{y-18}" text-anchor="middle" class="v">${v["median"]:.2f}</text>'
                    f'<text x="{x(v["p10"])-4:.1f}" y="{y+5}" text-anchor="end" class="s">{v["p10"]:.0f}</text>'
                    f'<text x="{x(v["p90"])+4:.1f}" y="{y+5}" class="s">{v["p90"]:.0f}</text>')
    lo_r, hi_r, perp = ev["offering"]["range_low"], ev["offering"]["range_high"], ev["perp"]["price"]
    marks = (f'<rect x="{x(lo_r):.1f}" y="28" width="{x(hi_r)-x(lo_r):.1f}" height="{H-60}" class="rng"/>'
             f'<text x="{x(lo_r):.1f}" y="{H-18}" class="s">filed range ${lo_r}-{hi_r}</text>'
             f'<line x1="{x(perp):.1f}" x2="{x(perp):.1f}" y1="28" y2="{H-32}" class="perp"/>'
             f'<text x="{x(perp)+4:.1f}" y="{H-18}" class="s">perp ${perp:.2f}</text>')
    for lo, hi, _ in br:
        for edge in (lo, hi):
            if lo_v < edge < hi_v:
                marks += f'<line x1="{x(edge):.1f}" x2="{x(edge):.1f}" y1="190" y2="{H-32}" class="pm"/>'
    svg = f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="ANT forecast ranges">{marks}{"".join(rows)}</svg>'
    path.write_text(f"""<!doctype html><meta charset="utf-8"><title>ANT IPO forecast</title>
<style>body{{font:14px system-ui,sans-serif;max-width:800px;margin:32px auto;padding:0 16px;color:#1f2328}}
.l{{font-size:13px;fill:#1f2328}}.v{{font-size:12px;font-weight:600;fill:#1f2328}}.s{{font-size:11px;fill:#6e7781}}
.band{{fill:#c96442;fill-opacity:.25;stroke:#c96442}}.med{{stroke:#9a3412;stroke-width:3}}
.rng{{fill:#0969da;fill-opacity:.08}}.perp{{stroke:#0969da;stroke-dasharray:4 3}}.pm{{stroke:#8250df;stroke-dasharray:2 3}}</style>
<h1>ANT IPO: four calls, 80% ranges</h1>
<p>Bars are p10 to p90, the dark tick is the median. Blue band: filed range. Blue dashed: Hyperliquid xyz:ANT perp.
Purple dotted (close and day 5 rows): Polymarket first-day market-cap bracket edges converted to price.</p>{svg}
<p style="color:#6e7781">Evidence as of {ev['as_of']}. Research and education only, not investment advice.</p>""")


def build_text(res, ev, cs, vix, tilt_note, probs, pm_note):
    o, b = ev["offering"], ev["book"]
    r = res
    thesis = (
        f"ANT prices above its ${o['range_low']}-${o['range_high']} range and opens near where offshore money already trades it. "
        f"The book is ~{b['oversubscription_x']:g}x covered and it guided to the top of the range or above (Bloomberg, 17 Nov) and now plans to raise the price (WSJ, 18 Nov), "
        f"so the model puts {probs['ipo_above']:.0%} on a price above ${o['range_high']} and a median of ${r['ipo_price']['median']:.2f}, "
        f"capped where banks would still leave ~8% headroom under the Hyperliquid xyz:ANT perp (${ev['perp']['price']:.2f}). "
        f"The open blends that perp, discounted for its offshore retail premium (it has fallen from $138 at listing), with a SpaceX-style "
        f"pop on the simulated IPO price, plus a first-trade shock as wide as past mega-IPO opens: median ${r['day1_open']['median']:.2f}. The close is simulated from {cs['n']} comparable mega-IPO "
        f"open-to-close moves, then tilted toward Polymarket's first-day market-cap brackets ({pm_note}); median ${r['day1_close']['median']:.2f}. "
        f"Day 5 adds comps' drift, a drag for the up-to-{o['first_day_lockup_release_shares']/1e6:.0f}M-share first-day lock-up release, "
        f"and greenshoe support below issue: median ${r['day5_close']['median']:.2f}. With a {o['float_pct']}% float and a "
        f"{o['retail_allocation_pct']}% retail slice, volatility is scaled by VIX ({vix:.1f}) and ranges are widened 15% because "
        f"pre-trade markets are under-dispersed. {tilt_note.capitalize()}.")
    risks = [
        f"Up to {o['first_day_lockup_release_shares']/1e6:.0f}M existing shares can sell on day one, about {o['first_day_lockup_release_shares']/o['shares_offered']:.0%} of the offering, which could cap the close and day 5",
        "The xyz:ANT perp is an offshore crypto derivative with no claim on shares; its premium has been volatile ($138 to ~$97) and may not converge to the Nasdaq open",
        f"A {o['float_pct']}% float with a {o['retail_allocation_pct']}% retail allocation makes the first days prone to squeezes or sharp fades beyond comparable IPOs",
        "Nvidia beat on 17 Nov but most of its 4.8% after-hours jump faded overnight; a weak AI tape on 19 Nov would hit the open",
        "An upsized deal or an early greenshoe exercise adds supply and lowers the pop for any given IPO price",
    ]
    return thesis, risks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="poulami95/market-triangulation")
    ap.add_argument("--repo", default="https://github.com/poulami95/anthropic-ipo-hackathon")
    ap.add_argument("--out", default=str(HERE))
    args = ap.parse_args()

    ev, cs = load_evidence(), comps_stats()
    as_of = datetime.fromisoformat(ev["as_of"].replace("Z", "+00:00"))
    vix, vix_date, vm = vix_multiplier()
    tilt, tilt_note = signal_tilt(as_of)
    log(f"evidence as of {ev['as_of']}; perp ${ev['perp']['price']:.2f} ({ev['perp']['as_of']}); book ~{ev['book']['oversubscription_x']}x, guidance {ev['book']['guidance']}")
    log(f"comps first-trade pop spread (open/IPO, log MAD): {cs['pop_sd']:.3f}")
    log(f"comps ({cs['names']}): open->close {cs['co_mu']:+.3f} +/- {cs['co_sd']:.3f}; close->day5 {cs['d5_mu']:+.3f} +/- {cs['d5_sd']:.3f} (log, median/MAD)")
    log(f"VIX {vix:.2f} on {vix_date} -> volatility x{vm:.2f}")
    log(tilt_note)
    nv = ev["nvidia"].get("after_hours_move_pct")
    log(f"Nvidia after-hours move: {'not yet set' if nv is None else f'{nv:+.1f}%'}")

    paths = simulate(ev, cs, vm, tilt)
    w, br, q, model = polymarket_weights(paths, ev)
    for (lo, hi, _), qi, mi in zip(br, q, model):
        log(f"close bracket ${lo:7.2f}-{'inf' if hi > 1e6 else f'${hi:.2f}'}: Polymarket {qi:.1%}  model-before-tilt {mi:.1%}")
    ess = 1 / sum(x * x for x in w)
    log(f"{len(paths):,} paths, effective sample after tilt {ess:,.0f}")

    res = summarise(paths, w)
    hi = ev["offering"]["range_high"]
    probs = {"ipo_above": prob(paths, w, lambda p: p[0] > hi + 1e-9),
             "open_above_100": prob(paths, w, lambda p: p[1] > 100),
             "day5_below_ipo": prob(paths, w, lambda p: p[3] < p[0])}
    pm_note = ", ".join(f"{b['prob']:.0%} {'<' if not b['lo'] else ''}${b['lo'] or b['hi']}T{'+' if b['hi'] is None else ''}"
                        + (f"-{b['hi']}T" if b['lo'] and b['hi'] else "") for b in ev["polymarket_close_mcap"]["brackets_usd_tn"])
    for k in KEYS:
        v = res[k]
        log(f"{k:11s} median ${v['median']:7.2f}   80% range ${v['p10']:7.2f} - ${v['p90']:7.2f}")
    log(f"P(IPO > ${hi}) {probs['ipo_above']:.0%}; P(open > $100) {probs['open_above_100']:.0%}; P(day 5 < IPO) {probs['day5_below_ipo']:.0%}")

    thesis, risks = build_text(res, ev, cs, vix, tilt_note, probs, pm_note)
    prediction = {
        "name": args.name,
        "method": "Monte Carlo triangulating order-book guidance, Hyperliquid pre-IPO perp and Polymarket brackets; Python stdlib",
        "repo_url": args.repo,
        **res,
        "thesis": thesis,
        "key_risks": risks,
        "confidence": 0.45,
        "extra_predictions": [
            {"statement": f"ANT's IPO is priced above ${hi}", "by_date": "2026-11-18", "probability": round(probs["ipo_above"], 2)},
            {"statement": "ANT's first trade is above $100", "by_date": "2026-11-19", "probability": round(probs["open_above_100"], 2)},
            {"statement": "ANT closes the fifth trading day below its IPO price", "by_date": "2026-11-25", "probability": round(probs["day5_below_ipo"], 2)},
        ],
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prediction.json").write_text(json.dumps(prediction, indent=2) + "\n")
    report_html(res, ev, br, out / "report.html")
    log(f"wrote prediction.json, report.html and run_log.txt in {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} at {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
    (out / "run_log.txt").write_text("\n".join(LOG) + "\n")
    validator = ROOT / "scripts/validate.py"
    if validator.exists():
        try:
            return subprocess.run([sys.executable, str(validator), str(out / "prediction.json")]).returncode
        except Exception as e:  # jsonschema missing: CI validates the pull request anyway
            print(f"validator not run: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
