# TQQQ Portfolio Strategy Backtest

Pure-Python replacement for the Lean-based backtest setup.

## Structure

```
strategies/
├── sim_engine.py         # Simulation + metrics engine (shared module)
├── run_backtest.py       # Pure Python backtest driver (replaces Lean + dotnet)
├── generate_report.py    # Interactive HTML report generator
├── download_data.py      # yfinance → Lean-format daily zip data downloader
└── README.md             # this file

Data/equity/usa/daily/    # Daily OHLCV zips (TQQQ, BTAL, GLD, XLP, CURE, SPY, QQQ, BIL)
output/<strategy>-<ts>/   # Timestamped backtest results (JSON + HTML + log)
```

## Usage

```bash
# 1. Refresh data (incremental from yfinance)
python3.11 strategies/download_data.py

# 2. Run backtest (default: 2011-09-14 → 2026-04-06)
python3.11 strategies/run_backtest.py

# Custom date range:
python3.11 strategies/run_backtest.py --start 2017-01-01 --end 2026-04-06

# Skip auto-opening the browser:
python3.11 strategies/run_backtest.py --no-open
```

## Strategies compared in the report

- **Base**: 35% TQQQ + 30% BTAL + 15% GLD + 15% XLP + 5% CURE (annual rebalance)
- **Timing1** (RV20 threshold variants: 22%, 25%, 20%, + RV60-22%): on monthly check, swap TQQQ↔QQQ based on QQQ realized vol
- **Timing2** (threshold variants: 22%, 25%, 20%): on monthly check, if RV>threshold split attack leg 75% QQQ / 25% TQQQ
- **Benchmarks**: 100% SPY, 100% QQQ, 50% BTAL + 50% TQQQ (annual rebalance)

All portfolios are simulated from raw daily price data using `sim_engine.py`.
No Lean, no dotnet, no external backtest framework.

## Interactive report features

- Client-side time range selector (rebase to $100K at selected start)
- Sorted hover (all portfolio values at cursor, descending)
- Max DD / Longest DD annotations that follow zoom
- Multi-tab layout: Summary, Metrics, Returns, Drawdown, Allocation, Rebalance Log
- Log scale toggle

## Dependencies

- Python 3.11
- `pandas`, `yfinance`, `curl_cffi` (for data download)
- `vectorbt` (installed but not required by current code — reserved for future parameter sweeps)
- No compiled dependencies besides numpy/pandas
