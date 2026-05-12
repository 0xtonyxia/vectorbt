#!/usr/bin/env python3.11
"""
Pure-Python backtest driver (replaces Lean).

Produces a Lean-compatible results JSON by running the base strategy through
our simulation engine, then invokes generate_report.py to build the HTML.

Output:  <repo>/output/TQQQPortfolioStrategy-<YYYYMMDD_HHMMSS>/
           ├── TQQQPortfolioStrategy.json     (Lean-shaped results)
           ├── TQQQPortfolioStrategy.html     (interactive report)
           └── TQQQPortfolioStrategy-log.txt  (rebalance log)

Usage:
    python3.11 run_backtest.py                # default 2011-09-14 → 2026-04-06
    python3.11 run_backtest.py --start 2017-01-01 --end 2026-04-06
    python3.11 run_backtest.py --no-open      # don't open browser
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT   = Path(__file__).resolve().parents[1]
DATA_DIR    = REPO_ROOT / "Data" / "equity" / "usa" / "daily"
OUTPUT_ROOT = REPO_ROOT / "output"
STRATEGIES  = Path(__file__).resolve().parent

# Make sim_engine importable. NOTE: backend selection (set_backend or
# SIM_BACKEND env var) must happen before any heavy use, which we do
# from main() based on CLI args.
sys.path.insert(0, str(STRATEGIES))
from sim_engine import (  # noqa: E402
    read_lean_daily,
    simulate_rebalance_portfolio,
    compute_stats,
    set_backend,
    get_backend,
)

STRATEGY_NAME = "TQQQPortfolioStrategy"
BASE_WEIGHTS  = {"TQQQ": 0.35, "BTAL": 0.30, "GLD": 0.15, "XLP": 0.15, "CURE": 0.05}
INITIAL_CASH  = 100_000


def parse_args():
    p = argparse.ArgumentParser(description="Run portfolio strategy backtest.")
    p.add_argument("--start", default="2011-09-14", help="Backtest start date (YYYY-MM-DD).")
    p.add_argument("--end",   default="2026-04-06", help="Backtest end date (YYYY-MM-DD).")
    p.add_argument(
        "--backend", choices=["python", "vectorbt"], default="python",
        help="Simulation backend (default: python, ~300x faster than vectorbt at our scale). "
             "Use 'vectorbt' if you want to leverage vbt.Portfolio APIs for future parameter sweeps.",
    )
    p.add_argument("--no-report", action="store_true", help="Skip HTML report generation.")
    p.add_argument("--no-open",   action="store_true", help="Do not auto-open HTML in browser.")
    return p.parse_args()


def to_iso_ts(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' to unix timestamp (UTC midnight)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return int(d.replace(tzinfo=timezone.utc).timestamp())


def build_lean_shaped_json(
    start: str,
    end:   str,
    eq_dates:   list[str],
    eq_vals:    list[float],
    bm_vals:    list[float],
    rebal_log:  list[dict],
) -> dict:
    """Build a results dict with the same shape Lean produces."""
    stats = compute_stats(eq_vals, eq_dates)

    def series_xy(vals):
        return [[to_iso_ts(d), v] for d, v in zip(eq_dates, vals)]

    def series_ohlc(vals):
        return [[to_iso_ts(d), v, v, v, v] for d, v in zip(eq_dates, vals)]

    # Daily pct returns
    ret_pts = []
    for i in range(1, len(eq_vals)):
        if eq_vals[i - 1] > 0:
            ret_pts.append([to_iso_ts(eq_dates[i]), (eq_vals[i] / eq_vals[i - 1] - 1)])

    # Drawdown
    peak = eq_vals[0]
    dd_pts = []
    for d, v in zip(eq_dates, eq_vals):
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        dd_pts.append([to_iso_ts(d), dd])

    return {
        "charts": {
            "Strategy Equity": {
                "series": {
                    "Equity": {"name": "Equity", "values": series_ohlc(eq_vals)},
                    "Return": {"name": "Return", "values": ret_pts},
                },
            },
            "Benchmark": {
                "series": {"Benchmark": {"name": "Benchmark", "values": series_xy(bm_vals)}},
            },
            "Drawdown": {
                "series": {"Equity Drawdown": {"name": "Equity Drawdown", "values": dd_pts}},
            },
            # Portfolio Margin placeholder (for Allocation chart)
            "Portfolio Margin": {"series": {}},
        },
        "statistics": {
            "Total Orders":                len(rebal_log),
            "Average Win":                 f"{stats.get('avg_dd', 0):.2f}%",
            "Average Loss":                "0%",
            "Compounding Annual Return":   f"{stats['cagr']:.3f}%",
            "Drawdown":                    f"{stats['max_dd']:.3f}%",
            "Expectancy":                  "0",
            "Start Equity":                str(INITIAL_CASH),
            "End Equity":                  f"{stats['end_val']:.2f}",
            "Net Profit":                  f"{stats['cumul_ret']:.3f}%",
            "Sharpe Ratio":                f"{stats['sharpe']:.3f}",
            "Sortino Ratio":               f"{stats['sortino']:.3f}",
            "Probabilistic Sharpe Ratio":  "0%",
            "Loss Rate":                   "0%",
            "Win Rate":                    "100%",
            "Profit-Loss Ratio":           "0",
            "Alpha":                       "0",
            "Beta":                        "0",
            "Annual Standard Deviation":   f"{stats['volatility']/100:.3f}",
            "Annual Variance":             f"{(stats['volatility']/100)**2:.3f}",
            "Information Ratio":           "0",
            "Tracking Error":              "0",
            "Treynor Ratio":               "0",
            "Total Fees":                  "$0.00",
            "Estimated Strategy Capacity": "$0",
            "Lowest Capacity Asset":       "TQQQ",
            "Portfolio Turnover":          "0%",
            "Drawdown Recovery":           "0",
        },
        "runtimeStatistics": {},
        "rollingWindow": {},
        "state": {},
        "algorithmConfiguration": {
            "Name": STRATEGY_NAME,
            "BacktestStart": start,
            "BacktestEnd": end,
        },
        "totalPerformance": {},
    }


def simulate_base_strategy(start: str, end: str, output_dir: Path):
    """Simulate the base 35/30/15/15/5 strategy, write JSON + log."""
    # Build common trading-day date list from SPY
    spy_px = read_lean_daily("SPY", DATA_DIR)
    dates = sorted(d for d in spy_px if start <= d <= end)
    if not dates:
        print(f"  ERROR: no SPY data in range {start} → {end}")
        sys.exit(1)

    # Load all prices we need for the base strategy + benchmark
    prices = {t: read_lean_daily(t, DATA_DIR) for t in list(BASE_WEIGHTS) + ["SPY"]}

    # Simulate main strategy (annual rebalance) + collect rebalance log
    eq_vals, rebal_log = simulate_rebalance_portfolio(
        BASE_WEIGHTS, prices, dates, initial=INITIAL_CASH, return_log=True,
    )

    # Simulate SPY benchmark buy-and-hold on same dates
    shares = 0.0
    bm_vals = []
    for d in dates:
        px = prices["SPY"].get(d)
        if px is None or px <= 0:
            bm_vals.append(bm_vals[-1] if bm_vals else INITIAL_CASH)
            continue
        if shares == 0:
            shares = INITIAL_CASH / px
        bm_vals.append(shares * px)

    # Build Lean-shaped results JSON
    results = build_lean_shaped_json(start, end, dates, eq_vals, bm_vals, rebal_log)
    results_path = output_dir / f"{STRATEGY_NAME}.json"
    with open(results_path, "w") as f:
        json.dump(results, f)

    # Write rebalance log
    log_path = output_dir / f"{STRATEGY_NAME}-log.txt"
    with open(log_path, "w") as f:
        for e in rebal_log:
            f.write(f"{e['date']}  Rebalanced  portfolio_value=${e['value']:,.2f}\n")

    print(f"\n{'─'*60}")
    print(f"  Base Strategy Backtest")
    print(f"{'─'*60}")
    print(f"  Date range:  {dates[0]} → {dates[-1]}  ({len(dates)} trading days)")
    print(f"  Start:       ${INITIAL_CASH:,.0f}")
    print(f"  End:         ${eq_vals[-1]:,.2f}")
    print(f"  Rebalances:  {len(rebal_log)}")

    return results_path


def run_report(results_path: Path, open_browser: bool):
    output_html = results_path.with_suffix(".html")
    report_script = STRATEGIES / "generate_report.py"
    subprocess.run([sys.executable, str(report_script), str(results_path), str(output_html)])
    if open_browser and output_html.exists():
        subprocess.run(["open", str(output_html)])


def main():
    args = parse_args()
    set_backend(args.backend)

    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / f"{STRATEGY_NAME}-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Propagate backend choice to the report subprocess via env var
    os.environ["SIM_BACKEND"] = args.backend

    print(f"  Strategy : {STRATEGY_NAME}")
    print(f"  Range    : {args.start} → {args.end}")
    print(f"  Backend  : {get_backend()}")
    print(f"  Output   : {output_dir}")

    results_path = simulate_base_strategy(args.start, args.end, output_dir)

    if not args.no_report:
        run_report(results_path, not args.no_open)

    # Symlink latest
    latest = OUTPUT_ROOT / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(output_dir.name)

    print(f"\n{'='*60}")
    print(f"  Backtest Complete")
    print(f"{'='*60}")
    print(f"  Output:  {output_dir}")
    print(f"  Latest:  output/latest → {output_dir.name}")
    for f in sorted(output_dir.iterdir()):
        size = f.stat().st_size
        unit = "KB" if size < 1_048_576 else "MB"
        val  = size / 1024 if unit == "KB" else size / 1_048_576
        print(f"    {f.name:45s} {val:>7.1f} {unit}")


if __name__ == "__main__":
    main()
