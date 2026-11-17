# Three clocks: an entry for the Anthropic IPO hackathon

A statistical model that turns three independent readings of where ANT will trade into the four prices the hackathon asks for. Python 3.10 or newer, standard library only, no API keys. Everything the entry needs is in this folder.

The method, data sources, backtest and limits are described in [`ENTRY.md`](ENTRY.md).

## Run it

All commands run from the repo root.

| Goal | Command |
|:---|:---|
| Reproduce the submitted file from the committed data | `python submissions/Neelabhra08/run.py` |
| Forecast again from live market data | `python submissions/Neelabhra08/run.py --refresh` |
| Refresh the data files only | `python submissions/Neelabhra08/build_data.py` |
| Replay the method on earlier listings | `python submissions/Neelabhra08/backtest.py` |
| Run the tests | `python -m unittest discover submissions/Neelabhra08/tests` |
| Validate the output (needs `pip install jsonschema`) | `python scripts/validate.py submissions/Neelabhra08/prediction.json` |

`run.py` prints a run log, and writes `prediction.json`, `run_log.txt` and three signals in `signals/` to this folder. `--out` sends them to another folder instead.

## Files

| File | Role |
|:---|:---|
| `run.py` | The one command. Runs the model, writes the prediction, the thesis text, the run log and the custom signals |
| `model.py` | The model: the three clocks, the kernel regressions over past IPOs and the joint simulation |
| `build_data.py` | Pulls live data and writes the derived files in `data/` |
| `sources.py` | The two public data sources: Hyperliquid for perpetuals, Yahoo chart data for stock prices |
| `backtest.py` | Walk-forward replay on the earlier listings that had a pre-IPO perpetual |
| `tests/test_model.py` | Behaviour checks, including that the score formula matches `scoring/score.py` |
| `data/` | Inputs. `comps.csv`, `deal.json` and `prediction_market.json` are maintained by hand with a source link per row. The rest is written by `build_data.py` |
| `signals/` | The three readings behind the forecast in the `signal.v1` format. Copy both files to `signals/custom/` at the repo root and run `python scripts/apply_weights.py` to merge them into the pack |

## Before the final run

1. Check SEC EDGAR for a new S-1/A. If the range changed, put the new range in `last_range` and the filing date in `last_range_filed_on` in `data/deal.json`. The model then prices off the new range.
2. Update the three probabilities and `read_at` in `data/prediction_market.json` from the current odds, or delete the file to run on two clocks.
3. Run `python submissions/Neelabhra08/run.py --refresh` and commit the refreshed `data/` files with the prediction, so the committed data reproduces the committed file.

The submission must be pushed before Anthropic announces its IPO price on the evening of 18 Nov (US Eastern time), or the IPO price call scores zero.

Last updated: 2026-11-18
