#!/usr/bin/env python3.11
"""
Simulation engine for portfolio backtests.

Backend selection
─────────────────
This module supports two backends for simulate_buy_and_hold and
simulate_rebalance_portfolio:

  - "python"   (default) — pure Python loops. ~300× faster than vectorbt
                          for our data scale (3660 days × 8 tickers).
                          ~5ms per backtest.

  - "vectorbt"           — vectorbt.Portfolio.from_orders with TargetPercent.
                          Slower for single backtests (~1.5s per call due
                          to Numba JIT + DataFrame overhead) but unlocks
                          built-in parameter sweep and vbt.Portfolio.stats().
                          Pick this when running many variants at once.

Selection priority (highest first):
  1. set_backend('vectorbt' | 'python') — programmatic
  2. SIM_BACKEND env var
  3. Default: 'python'

simulate_timing1, simulate_timing2, compute_stats, compute_beta,
compute_diversification_ratio are pure Python only — they do partial
per-asset rebalancing or use bespoke metric definitions that don't map
cleanly to vectorbt's API.
"""

import os
import zipfile
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_BACKEND = os.environ.get("SIM_BACKEND", "python").lower()
if _BACKEND not in ("python", "vectorbt"):
    _BACKEND = "python"


def set_backend(name: str) -> None:
    """Switch backend at runtime: 'python' or 'vectorbt'."""
    global _BACKEND
    name = name.lower()
    if name not in ("python", "vectorbt"):
        raise ValueError(f"Unknown backend: {name!r}. Use 'python' or 'vectorbt'.")
    _BACKEND = name


def get_backend() -> str:
    return _BACKEND


# Lazy import of vectorbt/pandas/numpy — only when vectorbt backend is used
_vbt = _np = _pd = None
def _ensure_vbt():
    global _vbt, _np, _pd
    if _vbt is None:
        import numpy as np
        import pandas as pd
        import vectorbt as vbt
        _np, _pd, _vbt = np, pd, vbt


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


# ===========================================================================
# Pure Python implementations
# ===========================================================================

def _bh_py(prices: dict, dates: list, initial: float) -> list[float]:
    eq, shares = [], 0.0
    for d in dates:
        px = prices.get(d)
        if px is None:
            eq.append(eq[-1] if eq else initial)
            continue
        if shares == 0 and px > 0:
            shares = initial / px
        eq.append(shares * px if shares > 0 else initial)
    return eq


def _rebal_py(weights: dict, all_prices: dict, dates: list,
              initial: float, return_log: bool):
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

    return (equity, log) if return_log else equity


# ===========================================================================
# vectorbt implementations
# ===========================================================================

def _prices_to_df(all_prices: dict, tickers: list, dates: list):
    _ensure_vbt()
    data = {t: [all_prices.get(t, {}).get(d, _np.nan) for d in dates] for t in tickers}
    df = _pd.DataFrame(data, index=_pd.to_datetime(dates))
    return df.ffill()


def _bh_vbt(prices: dict, dates: list, initial: float) -> list[float]:
    _ensure_vbt()
    if not dates:
        return []
    close = _pd.Series([prices.get(d, _np.nan) for d in dates],
                      index=_pd.to_datetime(dates)).ffill()
    if close.isna().all():
        return [initial] * len(dates)
    size = _pd.Series(_np.nan, index=close.index)
    first_idx = close.first_valid_index()
    if first_idx is not None:
        size.loc[first_idx] = 1.0
    pf = _vbt.Portfolio.from_orders(
        close=close, size=size, size_type='TargetPercent', init_cash=initial,
    )
    return pf.value().values.tolist()


def _rebal_vbt(weights: dict, all_prices: dict, dates: list,
               initial: float, return_log: bool):
    _ensure_vbt()
    tickers = list(weights.keys())
    dates_clean = [d for d in dates if all(all_prices.get(t, {}).get(d) for t in tickers)]
    if not dates_clean:
        return ([initial] * len(dates), []) if return_log else [initial] * len(dates)

    close = _prices_to_df(all_prices, tickers, dates_clean)
    size = _pd.DataFrame(_np.nan, index=close.index, columns=close.columns)
    last_year = None
    rebal_log = []
    for d in close.index:
        if last_year is None or d.year > last_year:
            for t, w in weights.items():
                size.at[d, t] = w
            last_year = d.year
            if return_log:
                rebal_log.append({"date": d.strftime("%Y-%m-%d"), "type": "annual",
                                  "value": None})

    pf = _vbt.Portfolio.from_orders(
        close=close, size=size, size_type='TargetPercent',
        init_cash=initial, group_by=True, cash_sharing=True, call_seq='auto',
    )
    eq_clean = pf.value().values.tolist()

    # Map back to original date list
    equity, j = [], 0
    clean_set = set(dates_clean)
    for d in dates:
        if d in clean_set:
            equity.append(eq_clean[j])
            j += 1
        else:
            equity.append(equity[-1] if equity else initial)

    if return_log:
        eq_map = dict(zip(dates_clean, eq_clean))
        for e in rebal_log:
            e["value"] = float(eq_map.get(e["date"], initial))
        return equity, rebal_log
    return equity


# ===========================================================================
# Public API: dispatcher → backend
# ===========================================================================

def simulate_buy_and_hold(prices: dict, dates: list, initial: float = 100_000) -> list[float]:
    if _BACKEND == "vectorbt":
        return _bh_vbt(prices, dates, initial)
    return _bh_py(prices, dates, initial)


def simulate_rebalance_portfolio(
    weights: dict,
    all_prices: dict,
    dates: list,
    initial: float = 100_000,
    return_log: bool = False,
):
    if _BACKEND == "vectorbt":
        return _rebal_vbt(weights, all_prices, dates, initial, return_log)
    return _rebal_py(weights, all_prices, dates, initial, return_log)


# ---------------------------------------------------------------------------
# Timing strategies (pure Python only — see module docstring)
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


def simulate_timing1(all_prices, dates, initial=100_000,
                     rv_window=20, rv_threshold=22.0, check_months=1,
                     base_weights=None):
    """Annual rebalance + periodic TQQQ↔QQQ swap based on QQQ's realized vol.

    The base_weights dict MUST contain a 'TQQQ' key (the attack leg that gets
    swapped). Other legs drift freely between annual rebalances.
    """
    if base_weights is None:
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
            rv = get_rv(d)
            shares = {}
            for t, w in base_weights.items():
                shares[t] = (total * w) / pxs[t]
            shares["QQQ"] = 0
            # Respect the current vol regime for the attack leg: if QQQ's RV is
            # above threshold at the rebalance date, hold the attack leg as QQQ
            # instead of snapping back to TQQQ (mirrors the monthly check).
            if rv > rv_threshold:
                tqqq_val = shares.get("TQQQ", 0) * pxs["TQQQ"]
                if tqqq_val > 0:
                    shares["QQQ"] = tqqq_val / pxs["QQQ"]
                    shares["TQQQ"] = 0
            last_rebal_year = year
            last_check_month = month
            months_since_check = 0
            after = _get_weights(shares, pxs, all_tickers)
            log.append({"date": d, "type": "annual", "before": before, "after": after,
                        "rv20": rv})

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
            rv20 = _get_qqq_rv20(qqq_sorted, qqq_map, d)
            shares = {}
            for t, w in base_weights.items():
                shares[t] = (total * w) / pxs[t]
            shares["QQQ"] = 0
            # Respect the current vol regime for the attack bucket: above
            # threshold, split the attack leg 25% TQQQ / 75% QQQ (mirrors the
            # monthly check) instead of snapping back to 100% TQQQ.
            if rv20 > rv_threshold:
                attack_total = shares.get("TQQQ", 0) * pxs["TQQQ"]
                if attack_total > 0:
                    shares["TQQQ"] = (attack_total * 0.25) / pxs["TQQQ"]
                    shares["QQQ"] = (attack_total * 0.75) / pxs["QQQ"]
            last_rebal_year = year
            after = _get_weights(shares, pxs, all_tickers)
            log.append({"date": d, "type": "annual", "before": before, "after": after,
                        "rv20": rv20})
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
# Metrics (pure Python — definitions fixed by our report contract)
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
