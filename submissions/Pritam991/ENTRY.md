# perp-anchor

- **Author(s):** @Pritam991
- **Repo:** https://github.com/Pritam991/anthropic-ipo-hackathon
- **Method and stack:** Plain Python 3, standard library only, no LLM
- **The idea:** Anchor day one on the market-implied price (pre-IPO perp) rather than IPO base rates, shrunk toward the one mega comp (SpaceX).
- **How it works:** IPO price = top of range lifted by the probability bankers price above it. Day-1 open/close = 60% perp (avg of $98 on 10 Nov, $103.93 on 12 Nov) + 40% SpaceX pop on the IPO price. Day 5 = day-1 close faded 4%, with a wide right-skewed range.
- **Evidence (dated, unverified beyond headlines where noted):** ~6x oversubscribed (Bloomberg, 12-13 Nov, via headlines); FT 11 Nov: bankers weigh pricing above range (headline only); Hyperliquid perp $98 (CNBC 10 Nov, range $94-$138); perp close $103.93 (Crypto Briefing headline); SpaceX $135 IPO, $160.95 close, ~$211 peak, later below IPO (Wikipedia).
- **Judgement calls, not measured:** P(above range)=0.55, perp weight 0.60, day-5 fade 4%, range widths. Edit the constants at the top of `run.py`.
- **Known gaps:** The signal pack (`signals/pack/`) was not available (gitignored upstream), so the 12k signals are not used. Web search was unavailable; Reuters/MarketWatch could not be fetched.
- **How to rerun:** `python submissions/Pritam991/run.py --name Pritam991/perp-anchor --repo https://github.com/Pritam991/anthropic-ipo-hackathon`; then `python scripts/validate.py submissions/Pritam991/prediction.json`
