#!/usr/bin/env python3.11
"""
Pure-Python simulation engine for portfolio backtests.

Contains all simulate_* and compute_* functions previously embedded in
generate_report.py. Shared by run_backtest.py and generate_report.py.

Input: price data read from Lean-format daily zip files.
Output: equity curves + metrics dicts (no Lean dependency).
"""

import zipfile
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def read_lean_daily(ticker: str, data_dir: Path) -> dict[str, float]:
    """Read a Lean daily zip → {date_str: close_price}."""
    zp = data_dir / f"{ticker.lower()}.zip"
    if not zp.exists():
        return {}
    with zipfile.ZipFile(zp) as zf:
        content = zf.read(zf.namelist()[0]).decode()
    prices = {}
    for line in content.strip().splitlines():
        parts = line.split(",")
        if len(parts) < 5:
            continue
        dt_str = parts[0][:8]
        d = f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:8]}"
        prices[d] = float(parts[4]) / 10_000
    return prices


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------

def _get_weights(shares: dict, pxs: dict, tickers: list) -> dict:
    total = sum(shares.get(t, 0) * pxs.get(t, 0) for t in tickers)
    if total <= 0:
        return {t: 0 for t in tickers}
    return {t: shares.get(t, 0) * pxs.get(t, 0) / total * 100 for t in tickers}


def _get_qqq_rv20(qqq_dates_sorted: list, qqq_by_date: dict, as_of: str) -> float:
    prior = [qqq_by_date[d] for d in qqq_dates_sorted if d <= as_of]
    if len(prior) < 21:
        return 0
    rets = [prior[i] / prior[i - 1] - 1 for i in range(-20, 0)]
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / len(rets)
    return (var ** 0.5) * (252 ** 0.5) * 100


# ---------------------------------------------------------------------------
# Portfolio simulations
# ---------------------------------------------------------------------------

def simulate_buy_and_hold(prices, dates, initial=100_000):
    equity, shares = [], 0.0
    for d in dates:
        px = prices.get(d)
        if px is None:
            equity.append(equity[-1] if equity else initial)
            continue
        if shares == 0 and px > 0:
            shares = initial / px
        equity.append(shares * px if shares > 0 else initial)
    return equity


def simulate_rebalance_portfolio(weights, all_prices, dates, initial=100_000, return_log=False):
    """Annual rebalance on year boundary.

    If return_log=True, returns (equity, log) where log contains rebalance events.
    Otherwise returns just the equity curve (backwards compatible).
    """
    tickers = list(weights.keys())
    equity, shares, last_yr = [], {}, None
    log = []

    for d in dates:
        pxs = {}
        missing = False
        for t in tickers:
            px = all_prices.get(t, {}).get(d)
            if px is None or px <= 0:
                missing = True
                break
            pxs[t] = px
        if missing:
            equity.append(equity[-1] if equity else initial)
            continue

        year = int(d[:4])
        need = (not shares) or (last_yr is not None and year > last_yr)
        if need:
            total = sum(shares.get(t, 0) * pxs[t] for t in tickers) if shares else initial
            if total <= 0:
                total = initial
            before = _get_weights(shares, pxs, tickers) if shares else {}
            for t in tickers:
                shares[t] = (total * weights[t]) / pxs[t]
            last_yr = year
            if return_log:
                after = _get_weights(shares, pxs, tickers)
                log.append({"date": d, "type": "annual", "value": total,
                            "before": before, "after": after})

        equity.append(sum(shares.get(t, 0) * pxs[t] for t in tickers))

    if return_log:
        return equity, log
    return equity


def simulate_timing1(all_prices, dates, initial=100_000,
                    rv_window=20, rv_threshold=22.0, check_months=1):
    """Timing1: RV > threshold → TQQQ→QQQ; else QQQ→TQQQ. Fixed legs unchanged between annuals."""
    base_weights = {"TQQQ": 0.35, "BTAL": 0.30, "GLD": 0.15, "XLP": 0.15, "CURE": 0.05}
    all_tickers = list(base_weights.keys()) + ["QQQ"]

    qqq_prices = all_prices.get("QQQ", {})
    qqq_sorted = sorted(d for d in qqq_prices if d <= dates[-1])
    qqq_map = {d: qqq_prices[d] for d in qqq_sorted}

    def get_rv(as_of):
        prior = [qqq_map[d] for d in qqq_sorted if d <= as_of]
        if len(prior) < rv_window + 1:
            return 0
        rets = [prior[i] / prior[i - 1] - 1 for i in range(-rv_window, 0)]
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        return (var ** 0.5) * (252 ** 0.5) * 100

    equity, log = [], []
    shares = {}
    last_rebal_year = None
    last_check_month = None
    months_since_check = 0

    for d in dates:
        pxs = {}
        missing = False
        for t in all_tickers:
            px = all_prices.get(t, {}).get(d)
            if px is None or px <= 0:
                missing = True
                break
            pxs[t] = px
        if missing:
            equity.append(equity[-1] if equity else initial)
            continue

        year, month = int(d[:4]), d[:7]
        first = not shares
        need_annual = first or (last_rebal_year is not None and year > last_rebal_year)

        new_month = (last_check_month is not None and month != last_check_month)
        need_check = False
        if first or need_annual:
            need_check = False
        elif new_month:
            months_since_check += 1
            if months_since_check >= check_months:
                need_check = True
                months_since_check = 0
            last_check_month = month

        if need_annual:
            total = sum(shares.get(t, 0) * pxs[t] for t in all_tickers) if shares else initial
            before = _get_weights(shares, pxs, all_tickers) if shares else {}
            shares = {}
            for t, w in base_weights.items():
                shares[t] = (total * w) / pxs[t]
            shares["QQQ"] = 0
            last_rebal_year = year
            last_check_month = month
            months_since_check = 0
            after = _get_weights(shares, pxs, all_tickers)
            log.append({"date": d, "type": "annual", "before": before, "after": after,
                        "rv20": get_rv(d)})

        elif need_check:
            rv = get_rv(d)
            before = _get_weights(shares, pxs, all_tickers)
            if rv > rv_threshold:
                tqqq_val = shares.get("TQQQ", 0) * pxs["TQQQ"]
                if tqqq_val > 0:
                    shares["QQQ"] = shares.get("QQQ", 0) + tqqq_val / pxs["QQQ"]
                    shares["TQQQ"] = 0
                    after = _get_weights(shares, pxs, all_tickers)
                    log.append({"date": d, "type": "check", "action": "TQQQ->QQQ",
                                "rv20": rv, "before": before, "after": after})
            else:
                qqq_val = shares.get("QQQ", 0) * pxs["QQQ"]
                if qqq_val > 0:
                    shares["TQQQ"] = shares.get("TQQQ", 0) + qqq_val / pxs["TQQQ"]
                    shares["QQQ"] = 0
                    after = _get_weights(shares, pxs, all_tickers)
                    log.append({"date": d, "type": "check", "action": "QQQ->TQQQ",
                                "rv20": rv, "before": before, "after": after})

        equity.append(sum(shares.get(t, 0) * pxs[t] for t in all_tickers))

    return equity, log


def simulate_timing2(all_prices, dates, initial=100_000, rv_threshold=22.0):
    """Timing2: RV > threshold → 75% QQQ + 25% TQQQ; else all TQQQ."""
    base_weights = {"TQQQ": 0.35, "BTAL": 0.30, "GLD": 0.15, "XLP": 0.15, "CURE": 0.05}
    all_tickers = list(base_weights.keys()) + ["QQQ"]

    qqq_prices = all_prices.get("QQQ", {})
    qqq_sorted = sorted(d for d in qqq_prices if d <= dates[-1])
    qqq_map = {d: qqq_prices[d] for d in qqq_sorted}

    equity, log = [], []
    shares = {}
    last_rebal_year = None
    last_check_month = None

    for d in dates:
        pxs = {}
        missing = False
        for t in all_tickers:
            px = all_prices.get(t, {}).get(d)
            if px is None or px <= 0:
                missing = True
                break
            pxs[t] = px
        if missing:
            equity.append(equity[-1] if equity else initial)
            continue

        year, month = int(d[:4]), d[:7]
        first = not shares
        need_annual = first or (last_rebal_year is not None and year > last_rebal_year)
        need_monthly = first or (last_check_month is not None and month != last_check_month)

        if need_annual:
            total = sum(shares.get(t, 0) * pxs[t] for t in all_tickers) if shares else initial
            before = _get_weights(shares, pxs, all_tickers) if shares else {}
            shares = {}
            for t, w in base_weights.items():
                shares[t] = (total * w) / pxs[t]
            shares["QQQ"] = 0
            last_rebal_year = year
            after = _get_weights(shares, pxs, all_tickers)
            log.append({"date": d, "type": "annual", "before": before, "after": after,
                        "rv20": _get_qqq_rv20(qqq_sorted, qqq_map, d)})
            last_check_month = month

        elif need_monthly:
            rv20 = _get_qqq_rv20(qqq_sorted, qqq_map, d)
            before = _get_weights(shares, pxs, all_tickers)
            tqqq_val = shares.get("TQQQ", 0) * pxs["TQQQ"]
            qqq_val = shares.get("QQQ", 0) * pxs["QQQ"]
            attack_total = tqqq_val + qqq_val

            if rv20 > rv_threshold:
                target_tqqq = attack_total * 0.25
                target_qqq = attack_total * 0.75
                if abs(tqqq_val - target_tqqq) > attack_total * 0.01:
                    shares["TQQQ"] = target_tqqq / pxs["TQQQ"] if pxs["TQQQ"] > 0 else 0
                    shares["QQQ"] = target_qqq / pxs["QQQ"] if pxs["QQQ"] > 0 else 0
                    after = _get_weights(shares, pxs, all_tickers)
                    log.append({"date": d, "type": "monthly", "action": "->25%TQQQ+75%QQQ",
                                "rv20": rv20, "before": before, "after": after})
            else:
                if qqq_val > attack_total * 0.01:
                    shares["TQQQ"] = attack_total / pxs["TQQQ"] if pxs["TQQQ"] > 0 else 0
                    shares["QQQ"] = 0
                    after = _get_weights(shares, pxs, all_tickers)
                    log.append({"date": d, "type": "monthly", "action": "->100%TQQQ",
                                "rv20": rv20, "before": before, "after": after})
            last_check_month = month

        equity.append(sum(shares.get(t, 0) * pxs[t] for t in all_tickers))

    return equity, log


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_stats(equity, dates):
    if len(equity) < 2:
        return {k: 0 for k in [
            "end_val", "cumul_ret", "cagr", "mwrr", "max_dd", "avg_dd",
            "longest_dd_yrs", "volatility", "sharpe", "sortino", "calmar",
            "ulcer", "upi", "beta", "dd_series",
            "max_dd_start", "max_dd_end", "longest_dd_start", "longest_dd_end",
            "max_dd_peak_idx", "max_dd_trough_idx", "max_dd_recov_idx",
            "longest_dd_start_idx", "longest_dd_end_idx",
        ]}

    start_val, end_val = equity[0], equity[-1]
    cumul_ret = (end_val / start_val - 1) * 100

    d0 = datetime.strptime(dates[0], "%Y-%m-%d")
    d1 = datetime.strptime(dates[-1], "%Y-%m-%d")
    years = (d1 - d0).days / 365.25
    cagr_val = ((end_val / start_val) ** (1 / years) - 1) * 100 if years > 0 else 0

    daily_rets = []
    for i in range(1, len(equity)):
        if equity[i - 1] > 0:
            r = equity[i] / equity[i - 1] - 1
            if r == 0 and equity[i] == equity[i - 1]:
                continue
            daily_rets.append(r)

    n = len(daily_rets) if daily_rets else 1
    mean_r = sum(daily_rets) / n if daily_rets else 0
    std_r = (sum((r - mean_r) ** 2 for r in daily_rets) / n) ** 0.5
    vol = std_r * (252 ** 0.5) * 100
    sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0

    downside_sq = sum(min(r, 0) ** 2 for r in daily_rets)
    downside_dev = (downside_sq / n) ** 0.5
    sortino = (mean_r / downside_dev) * (252 ** 0.5) if downside_dev > 0 else 0

    peak, peak_idx = equity[0], 0
    max_dd = 0.0
    max_dd_peak_idx = max_dd_trough_idx = 0
    dd_series_out = []
    longest_dd_days = 0
    longest_dd_start_idx = longest_dd_end_idx = 0
    current_dd_start_idx = current_dd_days = 0
    dd_depths = []

    for i, v in enumerate(equity):
        if v >= peak:
            peak = v
            peak_idx = i
            current_dd_days = 0
            current_dd_start_idx = i
        dd_pct = (v - peak) / peak * 100 if peak > 0 else 0
        dd_series_out.append(dd_pct)
        if dd_pct < 0:
            current_dd_days += 1
            dd_depths.append(abs(dd_pct))
            if current_dd_days > longest_dd_days:
                longest_dd_days = current_dd_days
                longest_dd_start_idx = current_dd_start_idx
                longest_dd_end_idx = i
        if abs(dd_pct) > max_dd:
            max_dd = abs(dd_pct)
            max_dd_peak_idx = peak_idx
            max_dd_trough_idx = i

    _peak_at_max_dd = equity[max_dd_peak_idx]
    max_dd_recov_idx = len(equity) - 1
    for i in range(max_dd_trough_idx + 1, len(equity)):
        if equity[i] >= _peak_at_max_dd:
            max_dd_recov_idx = i
            break

    longest_dd_yrs = round(longest_dd_days / 252, 2)
    avg_dd = sum(dd_depths) / len(dd_depths) if dd_depths else 0
    calmar = abs(cagr_val / max_dd) if max_dd > 0 else 0
    dd_sq_all = sum(d ** 2 for d in dd_series_out)
    ulcer = (dd_sq_all / len(dd_series_out)) ** 0.5 if dd_series_out else 0
    upi = cagr_val / ulcer if ulcer > 0 else 0

    return {
        "end_val": end_val, "cumul_ret": cumul_ret, "cagr": cagr_val, "mwrr": cagr_val,
        "max_dd": max_dd, "avg_dd": avg_dd, "longest_dd_yrs": longest_dd_yrs,
        "volatility": vol, "sharpe": sharpe, "sortino": sortino,
        "calmar": calmar, "ulcer": ulcer, "upi": upi, "beta": 0.0,
        "dd_series": dd_series_out,
        "max_dd_peak_idx": max_dd_peak_idx,
        "max_dd_trough_idx": max_dd_trough_idx,
        "max_dd_recov_idx": max_dd_recov_idx,
        "longest_dd_start_idx": longest_dd_start_idx,
        "longest_dd_end_idx": longest_dd_end_idx,
        "longest_dd_start": dates[longest_dd_start_idx] if longest_dd_start_idx < len(dates) else "",
        "longest_dd_end":   dates[longest_dd_end_idx]   if longest_dd_end_idx   < len(dates) else "",
        "max_dd_start":     dates[max_dd_peak_idx]      if max_dd_peak_idx      < len(dates) else "",
        "max_dd_end":       dates[max_dd_trough_idx]    if max_dd_trough_idx    < len(dates) else "",
    }


def compute_beta(equity, bench_equity):
    if len(equity) < 3 or len(bench_equity) < 3:
        return 0.0
    n = min(len(equity), len(bench_equity))
    r_a, r_b = [], []
    for i in range(1, n):
        if equity[i - 1] > 0 and bench_equity[i - 1] > 0:
            ra = equity[i] / equity[i - 1] - 1
            rb = bench_equity[i] / bench_equity[i - 1] - 1
            if ra == 0 and rb == 0:
                continue
            r_a.append(ra)
            r_b.append(rb)
    if len(r_b) < 2:
        return 0.0
    mean_b = sum(r_b) / len(r_b)
    mean_a = sum(r_a) / len(r_a)
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(r_a, r_b)) / len(r_a)
    var_b = sum((b - mean_b) ** 2 for b in r_b) / len(r_b)
    return cov / var_b if var_b > 0 else 0.0


def compute_diversification_ratio(weights, all_prices, dates):
    tickers = list(weights.keys())
    asset_rets = {t: [] for t in tickers}
    port_rets = []
    prev_px = {}
    for d in dates:
        pxs = {}
        ok = True
        for t in tickers:
            px = all_prices.get(t, {}).get(d)
            if px is None or px <= 0:
                ok = False
                break
            pxs[t] = px
        if not ok:
            continue
        if prev_px:
            for t in tickers:
                asset_rets[t].append(pxs[t] / prev_px[t] - 1)
            pr = sum(weights[t] * (pxs[t] / prev_px[t] - 1) for t in tickers)
            port_rets.append(pr)
        prev_px = pxs

    if len(port_rets) < 10:
        return 1.0
    n = len(port_rets)
    asset_vols = {}
    for t in tickers:
        rets = asset_rets[t]
        m = sum(rets) / len(rets)
        v = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5 * (252 ** 0.5)
        asset_vols[t] = v
    wavg_vol = sum(weights[t] * asset_vols[t] for t in tickers)
    pm = sum(port_rets) / n
    port_vol = (sum((r - pm) ** 2 for r in port_rets) / n) ** 0.5 * (252 ** 0.5)
    return wavg_vol / port_vol if port_vol > 0 else 1.0
