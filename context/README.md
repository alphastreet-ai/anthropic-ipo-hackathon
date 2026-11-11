# Context pack

Everything here is static. Agents may also use any other public data.

| Folder | What goes in it | Format |
|---|---|---|
| `anthropic-s1/` | The S-1 and amendments (link to EDGAR, text as markdown), key financial tables | `.md`, `.csv` |
| `macro-signals/` | AlphaStreet macro signals snapshot, with a data dictionary | `.csv` + `README.md` |
| `nvidia/` | Latest Nvidia earnings summary, guidance, AI capex news | `.md` |
| `comps/` | Recent tech and AI IPOs: filed range, final price, day-one open and close, close five trading days later | `.csv` |
| `markets/` | Prediction-market odds on the IPO, with the snapshot date | `.csv` |

The seed agents read every `.md`, `.txt`, `.csv` and `.json` file under `context/` (except `README.md` files).
