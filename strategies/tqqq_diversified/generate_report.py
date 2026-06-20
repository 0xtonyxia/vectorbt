#!/usr/bin/env python3.11
"""
Generate a professional interactive HTML backtest report.

Reads a Lean-shaped JSON (produced by run_backtest.py) plus the raw daily
price zips under Data/equity/usa/daily/, then emits a multi-tab HTML report
(Summary | Metrics | Returns | Drawdown | Allocation | Rebalance Log) with
client-side recompute on time-range changes.

Usage:
    python3.11 generate_report.py <results_json> [output_html]
"""

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Simulation engine + metric functions (shared with run_backtest.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sim_engine import (
    read_lean_daily as _read_lean_daily,
    simulate_buy_and_hold,
    simulate_rebalance_portfolio,
    simulate_timing1,
    simulate_timing2,
    compute_stats,
    compute_beta,
    compute_diversification_ratio,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR  = REPO_ROOT / "Data" / "equity" / "usa" / "daily"

results_json = Path(sys.argv[1])
orders_json  = results_json.with_name(results_json.stem + "-order-events.json")
output_html  = Path(sys.argv[2]) if len(sys.argv) > 2 else results_json.with_suffix(".html")

# Bind read_lean_daily to our data directory
def read_lean_daily(ticker):
    return _read_lean_daily(ticker, DATA_DIR)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
with open(results_json) as f:
    result = json.load(f)

orders_list = []
if orders_json.exists():
    with open(orders_json) as f:
        orders_list = json.load(f)

charts     = result["charts"]
statistics = result["statistics"]
rt_stats   = result.get("runtimeStatistics", {})
rolling    = result.get("rollingWindow", {})



# Build comparison portfolios
# Use strategy date range
_comp_prices = {
    t: read_lean_daily(t) for t in ["SPY", "QQQ", "TQQQ", "BTAL", "BIL", "GLD", "XLP", "CURE", "DBMF"]
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ts_to_iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

def extract_xy(series_data, key):
    pts = series_data.get(key, {}).get("values", [])
    return [ts_to_iso(p[0]) for p in pts], [p[1] for p in pts]

def extract_ohlc(series_data, key):
    pts = series_data.get(key, {}).get("values", [])
    return [ts_to_iso(p[0]) for p in pts], [p[4] if len(p) >= 5 else p[1] for p in pts]

def j(obj):
    """JSON-serialize, rounding floats to 2 decimals to reduce output size."""
    if isinstance(obj, list) and obj and isinstance(obj[0], float):
        return "[" + ",".join(f"{v:.2f}" for v in obj) + "]"
    return json.dumps(obj)

def pct(s):
    """Parse '23.017%' -> 23.017"""
    if isinstance(s, str) and s.endswith("%"):
        try: return float(s.rstrip("%"))
        except: pass
    try: return float(s)
    except: return 0.0

def fmt_pct(v, decimals=2):
    return f"{v:+.{decimals}f}%" if v != 0 else f"0.{'0'*decimals}%"

def fmt_dollar(v):
    return f"${v:,.0f}"

def fmt_num(v, decimals=2):
    return f"{v:.{decimals}f}"

# ---------------------------------------------------------------------------
# Parse equity & benchmark
# ---------------------------------------------------------------------------
eq_series = charts["Strategy Equity"]["series"]
_eq_dates_raw, _eq_vals_raw = extract_ohlc(eq_series, "Equity")

# Deduplicate: keep last value per date
_seen = {}
for d, v in zip(_eq_dates_raw, _eq_vals_raw):
    _seen[d] = v

# Filter to trading days only (dates where SPY has a price) to avoid
# weekend/holiday artifacts that break beta/volatility calculations
_spy_prices_for_filter = _comp_prices.get("SPY", {})
eq_dates, eq_vals = [], []
for d in _seen:
    if d in _spy_prices_for_filter:
        eq_dates.append(d)
        eq_vals.append(_seen[d])
# If no SPY filter available, fall back to all dates
if not eq_dates:
    eq_dates = list(_seen.keys())
    eq_vals  = list(_seen.values())

bm_series = charts["Benchmark"]["series"]
_bm_raw = {}
for p in bm_series.get("Benchmark", {}).get("values", []):
    d = ts_to_iso(p[0])
    _bm_raw[d] = p[1]
bm_dates = list(_bm_raw.keys())
bm_vals  = list(_bm_raw.values())

# Normalize benchmark to same starting value
if bm_vals and eq_vals:
    bm_scale = eq_vals[0] / bm_vals[0]
    bm_vals_norm = [v * bm_scale for v in bm_vals]
else:
    bm_vals_norm = bm_vals

# ---------------------------------------------------------------------------
# Comparison portfolios (computed from daily price data)
# ---------------------------------------------------------------------------
_initial = float(statistics.get("Start Equity", 100000))

def compute_annual_returns(dates, values):
    by_year = {}
    for d, v in zip(dates, values):
        yr = int(d[:4])
        by_year.setdefault(yr, []).append(v)
    years, rets = [], []
    prev_end = None
    for yr in sorted(by_year):
        end = by_year[yr][-1]
        start = prev_end if prev_end else by_year[yr][0]
        ret = (end / start - 1) * 100 if start else 0
        years.append(yr)
        rets.append(round(ret, 2))
        prev_end = end
    return years, rets

comp_spy_eq   = simulate_buy_and_hold(_comp_prices.get("SPY", {}), eq_dates, _initial)
comp_qqq_eq   = simulate_buy_and_hold(_comp_prices.get("QQQ", {}), eq_dates, _initial)
comp_bt50_eq  = simulate_rebalance_portfolio(
    {"BTAL": 0.50, "TQQQ": 0.50}, _comp_prices, eq_dates, _initial,
)
comp_t1_eq, t1_log     = simulate_timing1(_comp_prices, eq_dates, _initial, rv_window=20, rv_threshold=22, check_months=1)
comp_t1_25_eq, t1_25_log = simulate_timing1(_comp_prices, eq_dates, _initial, rv_window=20, rv_threshold=25, check_months=1)
comp_t1_20_eq, t1_20_log = simulate_timing1(_comp_prices, eq_dates, _initial, rv_window=20, rv_threshold=20, check_months=1)
comp_t1_rv60_eq, t1_rv60_log = simulate_timing1(_comp_prices, eq_dates, _initial, rv_window=60, rv_threshold=22, check_months=3)
comp_t2_eq, t2_log       = simulate_timing2(_comp_prices, eq_dates, _initial, rv_threshold=22)
comp_t2_25_eq, t2_25_log = simulate_timing2(_comp_prices, eq_dates, _initial, rv_threshold=25)
comp_t2_20_eq, t2_20_log = simulate_timing2(_comp_prices, eq_dates, _initial, rv_threshold=20)

# DBMF-base variants: same scheme, BTAL→DBMF and 35/30 → 25/40
_dbmf_base_weights = {"TQQQ": 0.25, "DBMF": 0.40, "GLD": 0.15, "XLP": 0.15, "CURE": 0.05}
comp_dbmf_strat_eq, dbmf_strat_log = simulate_rebalance_portfolio(
    _dbmf_base_weights, _comp_prices, eq_dates, _initial, return_log=True,
)
comp_t1d_22_eq, t1d_22_log = simulate_timing1(_comp_prices, eq_dates, _initial,
                                              rv_window=20, rv_threshold=22, check_months=1,
                                              base_weights=_dbmf_base_weights)
comp_t1d_25_eq, t1d_25_log = simulate_timing1(_comp_prices, eq_dates, _initial,
                                              rv_window=20, rv_threshold=25, check_months=1,
                                              base_weights=_dbmf_base_weights)
comp_t1d_20_eq, t1d_20_log = simulate_timing1(_comp_prices, eq_dates, _initial,
                                              rv_window=20, rv_threshold=20, check_months=1,
                                              base_weights=_dbmf_base_weights)

# DBBT base = 30% TQQQ + 20% DBMF + 15% BTAL + 15% GLD + 15% XLP + 5% CURE
# (split the hedge leg between DBMF managed futures and BTAL anti-beta)
_dbbt_base_weights = {"TQQQ": 0.30, "DBMF": 0.20, "BTAL": 0.15, "GLD": 0.15, "XLP": 0.15, "CURE": 0.05}
comp_dbbt_strat_eq, dbbt_strat_log = simulate_rebalance_portfolio(
    _dbbt_base_weights, _comp_prices, eq_dates, _initial, return_log=True,
)
comp_t1b_22_eq, t1b_22_log = simulate_timing1(_comp_prices, eq_dates, _initial,
                                              rv_window=20, rv_threshold=22, check_months=1,
                                              base_weights=_dbbt_base_weights)
comp_t1b_25_eq, t1b_25_log = simulate_timing1(_comp_prices, eq_dates, _initial,
                                              rv_window=20, rv_threshold=25, check_months=1,
                                              base_weights=_dbbt_base_weights)
comp_t1b_20_eq, t1b_20_log = simulate_timing1(_comp_prices, eq_dates, _initial,
                                              rv_window=20, rv_threshold=20, check_months=1,
                                              base_weights=_dbbt_base_weights)

# Simulate strategy from raw price data (same method as comparison portfolios)
# to avoid Lean equity curve timing artifacts that break beta/vol calculations
_strat_weights = {"TQQQ": 0.35, "BTAL": 0.30, "GLD": 0.15, "XLP": 0.15, "CURE": 0.05}
_all_strat_prices = {t: read_lean_daily(t) for t in _strat_weights}
comp_strat_eq = simulate_rebalance_portfolio(_strat_weights, _all_strat_prices, eq_dates, _initial)

# ---------------------------------------------------------------------------
# Reallocation helper (Reallocation tab): given a planned total $ amount and
# QQQ's RV20 as of the latest data date, show target holdings for the three
# production RV20-25% strategies. The attack leg (the "TQQQ" weight) is held as
# QQQ when RV20 > threshold, else TQQQ — mirroring the live strategy decision.
# ---------------------------------------------------------------------------
_REALLOC_THRESHOLD = 25.0
_realloc_strats = [
    {"name": "Timing1-BTAL-RV20-25%", "weights": _strat_weights},
    {"name": "Timing1-DBMF-RV20-25%", "weights": _dbmf_base_weights},
    {"name": "Timing1-DBBT-RV20-25%", "weights": _dbbt_base_weights},
]
_realloc_tickers = sorted({t for s in _realloc_strats for t in s["weights"]} | {"QQQ"})
_realloc_last_px = {}
for _t in _realloc_tickers:
    _pxs = _comp_prices.get(_t, {})
    if _pxs:
        _d = max(_pxs)
        _realloc_last_px[_t] = {"date": _d, "px": round(float(_pxs[_d]), 4)}
_realloc_data = {
    "threshold":    _REALLOC_THRESHOLD,
    "initial":      _initial,
    "dataLastDate": eq_dates[-1] if eq_dates else "",
    "strategies":   [{"name": s["name"], "weights": s["weights"]} for s in _realloc_strats],
    "lastPx":       _realloc_last_px,
}

# Built as a plain (non-f) string so the JS keeps normal single braces; injected
# into the main HTML f-string via the {_realloc_js} placeholder.
_realloc_js = "const REALLOC = " + json.dumps(_realloc_data) + ";\n" + r"""
function _rIsoToDate(s){ const p=s.split('-').map(Number); return new Date(p[0],p[1]-1,p[2]); }
function _rFmtDate(d){ const m=String(d.getMonth()+1).padStart(2,'0'), day=String(d.getDate()).padStart(2,'0'); return d.getFullYear()+'-'+m+'-'+day; }
function _rLastTradingDay(today){ const d=new Date(today.getFullYear(),today.getMonth(),today.getDate()); d.setDate(d.getDate()-1); while(d.getDay()===0||d.getDay()===6){ d.setDate(d.getDate()-1); } return d; }
function _rUSD(x){ return '$'+Math.round(x).toLocaleString('en-US'); }
function _rRV20(v,w){ if(!v||v.length<w+1)return 0; const r=[]; for(let i=v.length-w;i<v.length;i++){ r.push(v[i]/v[i-1]-1); } const m=r.reduce((a,b)=>a+b,0)/r.length; const va=r.reduce((a,b)=>a+(b-m)*(b-m),0)/r.length; return Math.sqrt(va)*Math.sqrt(252)*100; }

function renderRealloc(){
  const errBox=document.getElementById('realloc-error');
  const statusBox=document.getElementById('realloc-status');
  const resBox=document.getElementById('realloc-results');
  if(!resBox) return;

  // ── Data freshness ───────────────────────────────────────────────────
  // We can't know the live market-holiday calendar client-side, so we count
  // missing weekday sessions between the data's last date and "yesterday".
  // Tolerate ONE missing session (covers any single holiday, e.g. Juneteenth)
  // with a soft note; hard-error (prompt a re-run) on 2+ missing sessions or
  // data more than ~4 calendar days old — that can't be explained by a holiday.
  const today=new Date();
  const todayMid=new Date(today.getFullYear(),today.getMonth(),today.getDate());
  const dataLast=_rIsoToDate(REALLOC.dataLastDate);
  const expected=_rLastTradingDay(today);   // most recent weekday before today
  const staleDays=Math.round((todayMid-dataLast)/86400000);
  let missing=0;
  for(let d=new Date(dataLast.getTime()+86400000); d<=expected; d.setDate(d.getDate()+1)){
    const wd=d.getDay(); if(wd!==0&&wd!==6) missing++;
  }
  if(missing>=2 || staleDays>4){
    errBox.style.display='block';
    errBox.innerHTML='⚠ 报告数据已过期：数据截至 <b>'+REALLOC.dataLastDate+'</b>（距今 '+staleDays+' 天，缺少约 '+missing+' 个交易日，应更新至 ≥ '+_rFmtDate(expected)+'）。<br>请重新运行最新回测以下载最新数据：<code style="background:#fee2e2;padding:2px 6px;border-radius:4px">python3.11 strategies/tqqq_diversified/run_backtest.py</code>';
    statusBox.innerHTML='';
    resBox.innerHTML='';
    return;
  }
  errBox.style.display='none';

  // ── RV20 (QQQ, 20d) as of the latest data date → attack-leg decision
  const rv=_rRV20(V_qqq,20);
  const hi=rv>REALLOC.threshold;
  const attack=hi?'QQQ':'TQQQ';
  const lag=(missing===1)?' <span style="color:#b45309">（最近交易日 '+_rFmtDate(expected)+' 可能尚未包含——节假日或数据延迟；如需最新数据可重跑回测）</span>':'';
  statusBox.innerHTML='数据截至 <b>'+REALLOC.dataLastDate+'</b>'+lag+
    ' · QQQ RV20 = <b>'+rv.toFixed(1)+'%</b> '+(hi?'&gt;':'≤')+' 阈值 '+REALLOC.threshold+'%'+
    ' → 攻击腿持有 <b style="color:'+(hi?'#0891b2':'#dc2626')+'">'+attack+'</b>';

  const total=parseFloat((document.getElementById('realloc-amount')||{}).value)||0;
  let html='';
  REALLOC.strategies.forEach(function(s){
    let rows='';
    Object.keys(s.weights).forEach(function(t){
      const isAttack=(t==='TQQQ');
      const tk=isAttack?attack:t;
      const w=s.weights[t];
      const amt=total*w;
      const px=REALLOC.lastPx[tk];
      const sh=(px&&px.px>0)?(amt/px.px):null;
      rows+='<tr'+(isAttack?' style="background:#fffbeb"':'')+'>'+
        '<td style="padding:6px 10px;font-weight:'+(isAttack?'700':'500')+'">'+tk+(isAttack?' <span style="font-size:.7rem;color:#94a3b8">(attack leg)</span>':'')+'</td>'+
        '<td style="padding:6px 10px;text-align:right">'+(w*100).toFixed(0)+'%</td>'+
        '<td style="padding:6px 10px;text-align:right;font-variant-numeric:tabular-nums">'+_rUSD(amt)+'</td>'+
        '<td style="padding:6px 10px;text-align:right;color:#64748b;font-variant-numeric:tabular-nums">'+(sh!==null?sh.toFixed(2):'—')+'</td></tr>';
    });
    html+='<div class="card" style="margin-bottom:14px">'+
      '<div style="font-weight:700;font-size:1rem;margin-bottom:8px">'+s.name+'</div>'+
      '<table style="width:100%;border-collapse:collapse;font-size:.9rem">'+
      '<thead><tr style="color:#64748b;border-bottom:1px solid #e2e8f0">'+
      '<th style="padding:6px 10px;text-align:left">标的</th>'+
      '<th style="padding:6px 10px;text-align:right">权重</th>'+
      '<th style="padding:6px 10px;text-align:right">金额</th>'+
      '<th style="padding:6px 10px;text-align:right">股数 (最新收盘价)</th>'+
      '</tr></thead><tbody>'+rows+
      '<tr style="border-top:2px solid #e2e8f0;font-weight:700"><td style="padding:6px 10px">合计</td><td></td>'+
      '<td style="padding:6px 10px;text-align:right">'+_rUSD(total)+'</td><td></td></tr>'+
      '</tbody></table></div>';
  });
  resBox.innerHTML=html;
}
renderRealloc();
"""

comp_strat_stats = compute_stats(comp_strat_eq, eq_dates)  # from simulated equity
comp_spy_stats   = compute_stats(comp_spy_eq, eq_dates)
comp_qqq_stats   = compute_stats(comp_qqq_eq, eq_dates)
comp_bt50_stats  = compute_stats(comp_bt50_eq, eq_dates)
comp_t1_stats     = compute_stats(comp_t1_eq, eq_dates)
comp_t1_25_stats  = compute_stats(comp_t1_25_eq, eq_dates)
comp_t1_20_stats  = compute_stats(comp_t1_20_eq, eq_dates)
comp_t1_rv60_stats = compute_stats(comp_t1_rv60_eq, eq_dates)
comp_t2_stats     = compute_stats(comp_t2_eq, eq_dates)
comp_t2_25_stats  = compute_stats(comp_t2_25_eq, eq_dates)
comp_t2_20_stats  = compute_stats(comp_t2_20_eq, eq_dates)
comp_dbmf_strat_stats = compute_stats(comp_dbmf_strat_eq, eq_dates)
comp_t1d_22_stats     = compute_stats(comp_t1d_22_eq, eq_dates)
comp_t1d_25_stats     = compute_stats(comp_t1d_25_eq, eq_dates)
comp_t1d_20_stats     = compute_stats(comp_t1d_20_eq, eq_dates)
comp_dbbt_strat_stats = compute_stats(comp_dbbt_strat_eq, eq_dates)
comp_t1b_22_stats     = compute_stats(comp_t1b_22_eq, eq_dates)
comp_t1b_25_stats     = compute_stats(comp_t1b_25_eq, eq_dates)
comp_t1b_20_stats     = compute_stats(comp_t1b_20_eq, eq_dates)

# Compute beta vs SPY for all portfolios
comp_strat_stats["beta"]  = compute_beta(comp_strat_eq, comp_spy_eq)
comp_spy_stats["beta"]    = 1.00
comp_qqq_stats["beta"]    = compute_beta(comp_qqq_eq, comp_spy_eq)
comp_bt50_stats["beta"]   = compute_beta(comp_bt50_eq, comp_spy_eq)
comp_t1_stats["beta"]     = compute_beta(comp_t1_eq, comp_spy_eq)
comp_t1_25_stats["beta"]  = compute_beta(comp_t1_25_eq, comp_spy_eq)
comp_t1_20_stats["beta"]  = compute_beta(comp_t1_20_eq, comp_spy_eq)
comp_t1_rv60_stats["beta"] = compute_beta(comp_t1_rv60_eq, comp_spy_eq)
comp_t2_stats["beta"]     = compute_beta(comp_t2_eq, comp_spy_eq)
comp_t2_25_stats["beta"]  = compute_beta(comp_t2_25_eq, comp_spy_eq)
comp_t2_20_stats["beta"]  = compute_beta(comp_t2_20_eq, comp_spy_eq)
comp_dbmf_strat_stats["beta"] = compute_beta(comp_dbmf_strat_eq, comp_spy_eq)
comp_t1d_22_stats["beta"]     = compute_beta(comp_t1d_22_eq, comp_spy_eq)
comp_t1d_25_stats["beta"]     = compute_beta(comp_t1d_25_eq, comp_spy_eq)
comp_t1d_20_stats["beta"]     = compute_beta(comp_t1d_20_eq, comp_spy_eq)
comp_dbbt_strat_stats["beta"] = compute_beta(comp_dbbt_strat_eq, comp_spy_eq)
comp_t1b_22_stats["beta"]     = compute_beta(comp_t1b_22_eq, comp_spy_eq)
comp_t1b_25_stats["beta"]     = compute_beta(comp_t1b_25_eq, comp_spy_eq)
comp_t1b_20_stats["beta"]     = compute_beta(comp_t1b_20_eq, comp_spy_eq)

# Excess annualized return vs SPY
_spy_cagr = comp_spy_stats.get("cagr", 0)

# Diversification ratios
dr_strat   = compute_diversification_ratio(_strat_weights, _all_strat_prices, eq_dates)
dr_bt50    = compute_diversification_ratio({"BTAL": 0.50, "TQQQ": 0.50}, _comp_prices, eq_dates)
dr_dbmf_strat = compute_diversification_ratio(_dbmf_base_weights, _comp_prices, eq_dates)
dr_dbbt_strat = compute_diversification_ratio(_dbbt_base_weights, _comp_prices, eq_dates)
dr_t1 = dr_t1_25 = dr_t1_20 = dr_t1_rv60 = dr_t2 = 0.0  # timing: variable weights

# Drawdown series for all portfolios (negative values, 0% at top)
dd_strat   = comp_strat_stats.get("dd_series", [])
dd_t1      = comp_t1_stats.get("dd_series", [])
dd_t1_25   = comp_t1_25_stats.get("dd_series", [])
dd_t1_20   = comp_t1_20_stats.get("dd_series", [])
dd_t1_rv60 = comp_t1_rv60_stats.get("dd_series", [])
dd_t2      = comp_t2_stats.get("dd_series", [])
dd_t2_25   = comp_t2_25_stats.get("dd_series", [])
dd_t2_20   = comp_t2_20_stats.get("dd_series", [])
dd_dbmf_strat = comp_dbmf_strat_stats.get("dd_series", [])
dd_t1d_22  = comp_t1d_22_stats.get("dd_series", [])
dd_t1d_25  = comp_t1d_25_stats.get("dd_series", [])
dd_t1d_20  = comp_t1d_20_stats.get("dd_series", [])
dd_dbbt_strat = comp_dbbt_strat_stats.get("dd_series", [])
dd_t1b_22  = comp_t1b_22_stats.get("dd_series", [])
dd_t1b_25  = comp_t1b_25_stats.get("dd_series", [])
dd_t1b_20  = comp_t1b_20_stats.get("dd_series", [])
# Also replace equity values for charts with simulated (avoids Lean timing artifacts)
eq_vals = comp_strat_eq
dd_spy   = comp_spy_stats.get("dd_series", [])
dd_qqq   = comp_qqq_stats.get("dd_series", [])
dd_bt50  = comp_bt50_stats.get("dd_series", [])

# Precompute Y-axis floor for drawdown charts (avoids JS Math.min stack overflow)
_dd_min = min(
    min(dd_strat) if dd_strat else 0,
    min(dd_spy) if dd_spy else 0,
    min(dd_qqq) if dd_qqq else 0,
    min(dd_bt50) if dd_bt50 else 0,
    min(dd_t1) if dd_t1 else 0,
    min(dd_t1_25) if dd_t1_25 else 0,
    min(dd_t1_20) if dd_t1_20 else 0,
    min(dd_t1_rv60) if dd_t1_rv60 else 0,
    min(dd_t2) if dd_t2 else 0,
    min(dd_t2_25) if dd_t2_25 else 0,
    min(dd_t2_20) if dd_t2_20 else 0,
    min(dd_dbmf_strat) if dd_dbmf_strat else 0,
    min(dd_t1d_22) if dd_t1d_22 else 0,
    min(dd_t1d_25) if dd_t1d_25 else 0,
    min(dd_t1d_20) if dd_t1d_20 else 0,
    min(dd_dbbt_strat) if dd_dbbt_strat else 0,
    min(dd_t1b_22) if dd_t1b_22 else 0,
    min(dd_t1b_25) if dd_t1b_25 else 0,
    min(dd_t1b_20) if dd_t1b_20 else 0,
) * 1.05
_dd_range_json = j([round(_dd_min, 2), 2])

# Also compute annual returns for comparison portfolios
_, comp_spy_ann  = compute_annual_returns(eq_dates, comp_spy_eq) if comp_spy_eq else ([], [])
_, comp_qqq_ann  = compute_annual_returns(eq_dates, comp_qqq_eq) if comp_qqq_eq else ([], [])
_, comp_bt50_ann   = compute_annual_returns(eq_dates, comp_bt50_eq) if comp_bt50_eq else ([], [])
_, comp_t1_ann     = compute_annual_returns(eq_dates, comp_t1_eq) if comp_t1_eq else ([], [])
_, comp_t1_25_ann  = compute_annual_returns(eq_dates, comp_t1_25_eq) if comp_t1_25_eq else ([], [])
_, comp_t1_20_ann  = compute_annual_returns(eq_dates, comp_t1_20_eq) if comp_t1_20_eq else ([], [])
_, comp_t1_rv60_ann = compute_annual_returns(eq_dates, comp_t1_rv60_eq) if comp_t1_rv60_eq else ([], [])
_, comp_t2_ann     = compute_annual_returns(eq_dates, comp_t2_eq) if comp_t2_eq else ([], [])
_, comp_t2_25_ann  = compute_annual_returns(eq_dates, comp_t2_25_eq) if comp_t2_25_eq else ([], [])
_, comp_t2_20_ann  = compute_annual_returns(eq_dates, comp_t2_20_eq) if comp_t2_20_eq else ([], [])
_, comp_dbmf_strat_ann = compute_annual_returns(eq_dates, comp_dbmf_strat_eq) if comp_dbmf_strat_eq else ([], [])
_, comp_t1d_22_ann = compute_annual_returns(eq_dates, comp_t1d_22_eq) if comp_t1d_22_eq else ([], [])
_, comp_t1d_25_ann = compute_annual_returns(eq_dates, comp_t1d_25_eq) if comp_t1d_25_eq else ([], [])
_, comp_t1d_20_ann = compute_annual_returns(eq_dates, comp_t1d_20_eq) if comp_t1d_20_eq else ([], [])
_, comp_dbbt_strat_ann = compute_annual_returns(eq_dates, comp_dbbt_strat_eq) if comp_dbbt_strat_eq else ([], [])
_, comp_t1b_22_ann = compute_annual_returns(eq_dates, comp_t1b_22_eq) if comp_t1b_22_eq else ([], [])
_, comp_t1b_25_ann = compute_annual_returns(eq_dates, comp_t1b_25_eq) if comp_t1b_25_eq else ([], [])
_, comp_t1b_20_ann = compute_annual_returns(eq_dates, comp_t1b_20_eq) if comp_t1b_20_eq else ([], [])

# Daily returns (deduplicated)
_ret_seen = {}
for p in eq_series.get("Return", {}).get("values", []):
    d = ts_to_iso(p[0])
    _ret_seen[d] = p[1] * 100
ret_dates = list(_ret_seen.keys())
ret_vals  = list(_ret_seen.values())

# Drawdown
dd_series = charts["Drawdown"]["series"]
dd_dates, dd_vals = extract_xy(dd_series, "Equity Drawdown")
dd_vals = [v * 100 for v in dd_vals]

# Portfolio Margin (allocation)
margin_series = charts.get("Portfolio Margin", {}).get("series", {})

# ---------------------------------------------------------------------------
# Annual returns computation
# ---------------------------------------------------------------------------
ann_years, ann_strat = compute_annual_returns(eq_dates, eq_vals)

# Benchmark annual returns
ann_bm = []
if bm_dates and bm_vals:
    _, ann_bm = compute_annual_returns(bm_dates, bm_vals)

# Annual returns table data: year, strat_ret, strat_balance, bm_ret, bm_balance
ann_table = []
strat_bal = float(statistics.get("Start Equity", 100000))
bm_bal = strat_bal
for i, yr in enumerate(ann_years):
    sr = ann_strat[i]
    new_strat = strat_bal * (1 + sr / 100)
    br = ann_bm[i] if i < len(ann_bm) else 0
    new_bm = bm_bal * (1 + br / 100)
    ann_table.append({
        "year": yr, "strat_ret": sr, "strat_bal": new_strat,
        "bm_ret": br, "bm_bal": new_bm,
    })
    strat_bal = new_strat
    bm_bal = new_bm

# ---------------------------------------------------------------------------
# Return distribution stats
# ---------------------------------------------------------------------------
nonzero_rets = [r for r in ret_vals if r != 0]
nonzero_rets.sort()

def percentile(data, p):
    if not data: return 0
    k = (len(data) - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c: return data[f]
    return data[f] * (c - k) + data[c] * (k - f)

if nonzero_rets:
    dist_stats = {
        "5th Percentile": percentile(nonzero_rets, 5),
        "25th Percentile": percentile(nonzero_rets, 25),
        "50th Percentile (Median)": percentile(nonzero_rets, 50),
        "75th Percentile": percentile(nonzero_rets, 75),
        "95th Percentile": percentile(nonzero_rets, 95),
        "Mean": sum(nonzero_rets) / len(nonzero_rets),
        "Std Deviation": (sum((r - sum(nonzero_rets)/len(nonzero_rets))**2 for r in nonzero_rets) / len(nonzero_rets)) ** 0.5,
        "Kurtosis": 0,
        "Best Day": max(nonzero_rets),
        "Worst Day": min(nonzero_rets),
        "# Positive Days": sum(1 for r in nonzero_rets if r > 0),
        "# Negative Days": sum(1 for r in nonzero_rets if r < 0),
    }
else:
    dist_stats = {}

# ---------------------------------------------------------------------------
# Top-level stats for summary row
# ---------------------------------------------------------------------------
start_eq = float(statistics.get("Start Equity", 100000))
end_eq   = float(statistics.get("End Equity", 0))
cagr     = pct(statistics.get("Compounding Annual Return", "0"))
max_dd   = pct(statistics.get("Drawdown", "0"))
sharpe   = float(statistics.get("Sharpe Ratio", 0))
sortino  = float(statistics.get("Sortino Ratio", 0))
volatility = pct(statistics.get("Annual Standard Deviation", "0"))
calmar   = abs(cagr / max_dd) if max_dd != 0 else 0
beta     = float(statistics.get("Beta", 0))
alpha    = float(statistics.get("Alpha", 0))

date_start = eq_dates[0] if eq_dates else ""
date_end   = eq_dates[-1] if eq_dates else ""

# Years
if date_start and date_end:
    d0 = datetime.strptime(date_start, "%Y-%m-%d")
    d1 = datetime.strptime(date_end, "%Y-%m-%d")
    years_span = round((d1 - d0).days / 365.25, 2)
else:
    years_span = 0

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
TICKER_COLORS = {
    "TQQQ": "#6366f1", "BTAL": "#f97316", "GLD": "#eab308",
    "XLP": "#22c55e", "CURE": "#ec4899", "SPY": "#64748b",
    "DBMF": "#3b82f6",
}

def ret_color(v, light=False):
    """Return CSS background color for a return value (heatmap style)."""
    if v > 30: return "#15803d" if not light else "#bbf7d0"
    if v > 20: return "#16a34a" if not light else "#bbf7d0"
    if v > 10: return "#22c55e" if not light else "#dcfce7"
    if v > 0:  return "#86efac" if not light else "#f0fdf4"
    if v > -10: return "#fca5a5" if not light else "#fef2f2"
    if v > -20: return "#ef4444" if not light else "#fee2e2"
    return "#dc2626" if not light else "#fecaca"

def ret_text_color(v):
    if abs(v) > 15: return "#fff"
    if v > 0: return "#14532d"
    if v < 0: return "#7f1d1d"
    return "#334155"

# ---------------------------------------------------------------------------
# Metrics table data
# ---------------------------------------------------------------------------
METRICS_ROWS = [
    ("section", "Returns"),
    ("metric", "Cumulative Return", f"{pct(statistics.get('Net Profit','0')):.2f}%"),
    ("metric", "CAGR", f"{cagr:.2f}%"),
    ("metric", "Best Year", f"{max(ann_strat):.2f}%" if ann_strat else "N/A"),
    ("metric", "Worst Year", f"{min(ann_strat):.2f}%" if ann_strat else "N/A"),
    ("metric", "Best Day", f"{dist_stats.get('Best Day',0):.2f}%"),
    ("metric", "Worst Day", f"{dist_stats.get('Worst Day',0):.2f}%"),
    ("metric", "Average Win", statistics.get("Average Win", "")),
    ("metric", "Average Loss", statistics.get("Average Loss", "")),
    ("metric", "Win Rate", statistics.get("Win Rate", "")),
    ("metric", "Loss Rate", statistics.get("Loss Rate", "")),
    ("metric", "Profit-Loss Ratio", statistics.get("Profit-Loss Ratio", "")),
    ("section", "Risk"),
    ("metric", "Annual Volatility", f"{volatility:.2f}%"),
    ("metric", "Max Drawdown", f"{max_dd:.2f}%"),
    ("metric", "Drawdown Recovery (days)", statistics.get("Drawdown Recovery", "")),
    ("metric", "Sharpe Ratio", f"{sharpe:.2f}"),
    ("metric", "Sortino Ratio", f"{sortino:.2f}"),
    ("metric", "Calmar Ratio", f"{calmar:.2f}"),
    ("metric", "Excess Return vs SPY", f"{cagr - _spy_cagr:+.2f}%"),
    ("metric", "Beta", f"{beta:.2f}"),
    ("metric", "Information Ratio", statistics.get("Information Ratio", "")),
    ("metric", "Tracking Error", statistics.get("Tracking Error", "")),
    ("metric", "Treynor Ratio", statistics.get("Treynor Ratio", "")),
    ("metric", "Probabilistic Sharpe", statistics.get("Probabilistic Sharpe Ratio", "")),
    ("section", "Portfolio"),
    ("metric", "Total Orders", statistics.get("Total Orders", "")),
    ("metric", "Total Fees", statistics.get("Total Fees", "")),
    ("metric", "Portfolio Turnover", statistics.get("Portfolio Turnover", "")),
    ("metric", "Start Equity", fmt_dollar(start_eq)),
    ("metric", "End Equity", fmt_dollar(end_eq)),
    ("metric", "Net Profit", fmt_dollar(end_eq - start_eq)),
]

def build_metrics_html():
    rows = []
    for item in METRICS_ROWS:
        if item[0] == "section":
            rows.append(f'<tr class="section-row"><td colspan="2">{item[1]}</td></tr>')
        else:
            rows.append(f'<tr><td>{item[1]}</td><td class="val">{item[2]}</td></tr>')
    return "\n".join(rows)

# ---------------------------------------------------------------------------
# Annual returns table HTML
# ---------------------------------------------------------------------------
def build_annual_table_html():
    rows = []
    for r in reversed(ann_table):
        bg_s = ret_color(r["strat_ret"], light=True)
        tc_s = ret_text_color(r["strat_ret"])
        bg_b = ret_color(r["bm_ret"], light=True)
        tc_b = ret_text_color(r["bm_ret"])
        rows.append(f'''<tr>
            <td class="yr">{r["year"]}</td>
            <td style="background:{bg_s};color:{tc_s}" class="val">{r["strat_ret"]:+.2f}%</td>
            <td class="val">{fmt_dollar(r["strat_bal"])}</td>
            <td style="background:{bg_b};color:{tc_b}" class="val">{r["bm_ret"]:+.2f}%</td>
            <td class="val">{fmt_dollar(r["bm_bal"])}</td>
        </tr>''')
    return "\n".join(rows)

# ---------------------------------------------------------------------------
# Distribution stats table
# ---------------------------------------------------------------------------
def build_dist_table():
    rows = []
    for label, val in dist_stats.items():
        if label.startswith("#"):
            rows.append(f'<tr><td>{label}</td><td class="val">{int(val)}</td></tr>')
        else:
            rows.append(f'<tr><td>{label}</td><td class="val">{val:.4f}%</td></tr>')
    return "\n".join(rows)

# ---------------------------------------------------------------------------
# Rebalance log HTML
# ---------------------------------------------------------------------------
def _fmt_weights(w: dict) -> str:
    if not w:
        return "-"
    parts = []
    for t in ["TQQQ", "QQQ", "BTAL", "DBMF", "GLD", "XLP", "CURE"]:
        v = w.get(t, 0)
        if v > 0.5:
            parts.append(f"{t}:{v:.1f}%")
    return " ".join(parts)

def _build_log_html(label: str, log: list[dict], color: str) -> str:
    if not log:
        return ""
    rows = []
    for entry in log:
        typ = entry.get("type", "")
        action = entry.get("action", "rebalance")
        rv20 = entry.get("rv20", 0)
        before = _fmt_weights(entry.get("before", {}))
        after = _fmt_weights(entry.get("after", {}))
        badge_cls = "badge-green" if typ == "annual" else "badge-purple"
        rows.append(
            f'<tr><td>{entry["date"]}</td>'
            f'<td><span class="badge {badge_cls}">{typ}</span></td>'
            f'<td>{action}</td>'
            f'<td class="val">{rv20:.1f}%</td>'
            f'<td style="font-size:.7rem">{before}</td>'
            f'<td style="font-size:.7rem">{after}</td></tr>'
        )
    return f"""
    <div class="section-title" style="color:{color}">{label}</div>
    <div class="tbl-wrap" style="max-height:400px">
    <table class="tbl">
    <thead><tr><th>Date</th><th>Type</th><th>Action</th><th class="val">RV20</th><th>Before</th><th>After</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
    </table></div>"""

log_html  = _build_log_html("Timing1-BTAL-RV20-22%", t1_log, "#d946ef")
log_html += _build_log_html("Timing1-BTAL-RV20-25%", t1_25_log, "#a855f7")
log_html += _build_log_html("Timing1-BTAL-RV20-20%", t1_20_log, "#ec4899")
log_html += _build_log_html("Timing1-BTAL-RV60-22%", t1_rv60_log, "#14b8a6")
log_html += _build_log_html("Timing2-BTAL-RV20-22%", t2_log, "#b45309")
log_html += _build_log_html("Timing2-BTAL-RV20-25%", t2_25_log, "#92400e")
log_html += _build_log_html("Timing2-BTAL-RV20-20%", t2_20_log, "#78350f")
log_html += _build_log_html("Timing1-DBMF-RV20-22%", t1d_22_log, "#3b82f6")
log_html += _build_log_html("Timing1-DBMF-RV20-25%", t1d_25_log, "#0891b2")
log_html += _build_log_html("Timing1-DBMF-RV20-20%", t1d_20_log, "#0d9488")
log_html += _build_log_html("Timing1-DBBT-RV20-22%", t1b_22_log, "#e11d48")
log_html += _build_log_html("Timing1-DBBT-RV20-25%", t1b_25_log, "#f43f5e")
log_html += _build_log_html("Timing1-DBBT-RV20-20%", t1b_20_log, "#fb7185")

# ---------------------------------------------------------------------------
# Portfolio registry for table/chart generation
# ---------------------------------------------------------------------------
_portfolios = [
    # Benchmarks first
    {"name": "100% SPY", "color": "#f59e0b", "stats": comp_spy_stats, "eq": comp_spy_eq, "dd": dd_spy, "ann": comp_spy_ann, "dr": 1.0, "bg": ""},
    {"name": "100% QQQ", "color": "#06b6d4", "stats": comp_qqq_stats, "eq": comp_qqq_eq, "dd": dd_qqq, "ann": comp_qqq_ann, "dr": 1.0, "bg": ""},
    {"name": "50%BTAL+50%TQQQ", "color": "#22c55e", "stats": comp_bt50_stats, "eq": comp_bt50_eq, "dd": dd_bt50, "ann": comp_bt50_ann, "dr": dr_bt50, "bg": ""},
    # BTAL family
    {"name": "35tqqq+30btal+15gld+15xlp+5cure", "color": "#7c3aed", "stats": comp_strat_stats, "eq": comp_strat_eq, "dd": dd_strat, "ann": ann_strat, "dr": dr_strat, "bg": "#f5f3ff"},
    {"name": "Timing1-BTAL-RV20-22%", "color": "#d946ef", "stats": comp_t1_stats, "eq": comp_t1_eq, "dd": dd_t1, "ann": comp_t1_ann, "dr": 0, "bg": "#fdf4ff"},
    {"name": "Timing1-BTAL-RV20-25%", "color": "#a855f7", "stats": comp_t1_25_stats, "eq": comp_t1_25_eq, "dd": dd_t1_25, "ann": comp_t1_25_ann, "dr": 0, "bg": "#faf5ff"},
    {"name": "Timing1-BTAL-RV20-20%", "color": "#ec4899", "stats": comp_t1_20_stats, "eq": comp_t1_20_eq, "dd": dd_t1_20, "ann": comp_t1_20_ann, "dr": 0, "bg": "#fdf2f8"},
    {"name": "Timing1-BTAL-RV60-22%", "color": "#14b8a6", "stats": comp_t1_rv60_stats, "eq": comp_t1_rv60_eq, "dd": dd_t1_rv60, "ann": comp_t1_rv60_ann, "dr": 0, "bg": "#f0fdfa"},
    {"name": "Timing2-BTAL-RV20-22%", "color": "#b45309", "stats": comp_t2_stats, "eq": comp_t2_eq, "dd": dd_t2, "ann": comp_t2_ann, "dr": 0, "bg": "#fef3c7"},
    {"name": "Timing2-BTAL-RV20-25%", "color": "#92400e", "stats": comp_t2_25_stats, "eq": comp_t2_25_eq, "dd": dd_t2_25, "ann": comp_t2_25_ann, "dr": 0, "bg": "#fefce8"},
    {"name": "Timing2-BTAL-RV20-20%", "color": "#78350f", "stats": comp_t2_20_stats, "eq": comp_t2_20_eq, "dd": dd_t2_20, "ann": comp_t2_20_ann, "dr": 0, "bg": "#fffbeb"},
    # DBMF family
    {"name": "25tqqq+40dbmf+15gld+15xlp+5cure", "color": "#1e40af", "stats": comp_dbmf_strat_stats, "eq": comp_dbmf_strat_eq, "dd": dd_dbmf_strat, "ann": comp_dbmf_strat_ann, "dr": dr_dbmf_strat, "bg": "#dbeafe"},
    {"name": "Timing1-DBMF-RV20-22%", "color": "#3b82f6", "stats": comp_t1d_22_stats, "eq": comp_t1d_22_eq, "dd": dd_t1d_22, "ann": comp_t1d_22_ann, "dr": 0, "bg": "#eff6ff"},
    {"name": "Timing1-DBMF-RV20-25%", "color": "#0891b2", "stats": comp_t1d_25_stats, "eq": comp_t1d_25_eq, "dd": dd_t1d_25, "ann": comp_t1d_25_ann, "dr": 0, "bg": "#cffafe"},
    {"name": "Timing1-DBMF-RV20-20%", "color": "#0d9488", "stats": comp_t1d_20_stats, "eq": comp_t1d_20_eq, "dd": dd_t1d_20, "ann": comp_t1d_20_ann, "dr": 0, "bg": "#ccfbf1"},
    # DBBT family
    {"name": "30tqqq+20dbmf+15btal+15gld+15xlp+5cure", "color": "#9f1239", "stats": comp_dbbt_strat_stats, "eq": comp_dbbt_strat_eq, "dd": dd_dbbt_strat, "ann": comp_dbbt_strat_ann, "dr": dr_dbbt_strat, "bg": "#ffe4e6"},
    {"name": "Timing1-DBBT-RV20-22%", "color": "#e11d48", "stats": comp_t1b_22_stats, "eq": comp_t1b_22_eq, "dd": dd_t1b_22, "ann": comp_t1b_22_ann, "dr": 0, "bg": "#fff1f2"},
    {"name": "Timing1-DBBT-RV20-25%", "color": "#f43f5e", "stats": comp_t1b_25_stats, "eq": comp_t1b_25_eq, "dd": dd_t1b_25, "ann": comp_t1b_25_ann, "dr": 0, "bg": "#ffe4e6"},
    {"name": "Timing1-DBBT-RV20-20%", "color": "#fb7185", "stats": comp_t1b_20_stats, "eq": comp_t1b_20_eq, "dd": dd_t1b_20, "ann": comp_t1b_20_ann, "dr": 0, "bg": "#fff1f2"},
]

def _build_stats_table_rows():
    rows = []
    for p in _portfolios:
        s = p["stats"]
        bg = f' style="background:{p["bg"]}"' if p["bg"] else ""
        rows.append(f'''<tr{bg}>
  <td style="font-weight:600;color:{p["color"]}">{p["name"]}</td>
  <td class="val">{fmt_dollar(s.get('end_val',0))}</td>
  <td class="val">{s.get('cumul_ret',0):.2f}%</td>
  <td class="val">{s.get('cagr',0):.2f}%</td>
  <td class="val" title="{s.get('max_dd_start','')} to {s.get('max_dd_end','')}" style="cursor:help">-{s.get('max_dd',0):.2f}%</td>
  <td class="val">-{s.get('avg_dd',0):.2f}%</td>
  <td class="val" title="{s.get('longest_dd_start','')} to {s.get('longest_dd_end','')}" style="cursor:help">{s.get('longest_dd_yrs',0):.2f} yrs</td>
  <td class="val">{s.get('volatility',0):.2f}%</td>
  <td class="val">{s.get('sharpe',0):.2f}</td>
  <td class="val">{s.get('sortino',0):.2f}</td>
  <td class="val">{s.get('calmar',0):.2f}</td>
  <td class="val">{s.get('ulcer',0):.2f}</td>
  <td class="val">{s.get('upi',0):.2f}</td>
  <td class="val">{s.get('beta',0):.2f}</td>
</tr>''')
    return "\n".join(rows)

stats_table_rows = _build_stats_table_rows()

# ---------------------------------------------------------------------------
# Allocation traces for Plotly
# ---------------------------------------------------------------------------
margin_traces_js = ""
if margin_series:
    margin_dates_data = None
    for t in margin_series:
        pts = margin_series[t].get("values", [])
        dates_t = [ts_to_iso(p[0]) for p in pts]
        vals_t  = [p[1] for p in pts]
        if margin_dates_data is None and dates_t:
            margin_dates_data = dates_t
        color = TICKER_COLORS.get(t.replace(" ", ""), "#94a3b8")
        margin_traces_js += f"""{{
            x: {j(dates_t)}, y: {j(vals_t)}, name: {j(t)},
            type:'scatter', mode:'lines', stackgroup:'one',
            line:{{width:0}}, fillcolor:{j(color)},
        }},"""

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report — {results_json.stem}</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
/* ── Reset & Base ──────────────────────────────────────── */
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',system-ui,-apple-system,sans-serif;background:#f8fafc;color:#1e293b;font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}}

/* ── Header ────────────────────────────────────────────── */
.hdr{{background:#fff;border-bottom:1px solid #e2e8f0;padding:20px 32px}}
.hdr h1{{font-size:1.5rem;font-weight:700;color:#0f172a}}
.hdr h1 span{{color:#64748b;font-weight:400;font-size:.95rem;margin-left:8px}}
.hdr .sub{{color:#64748b;font-size:.8rem;margin-top:4px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.badge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.75rem;font-weight:600}}
.badge-green{{background:#dcfce7;color:#15803d}}
.badge-purple{{background:#f3e8ff;color:#7c3aed}}

/* ── Stats Row ─────────────────────────────────────────── */
.stats-row{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:1px;background:#e2e8f0;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin:20px 32px 0}}
.stat-cell{{background:#fff;padding:12px 16px;text-align:center}}
.stat-cell .label{{font-size:.7rem;color:#64748b;text-transform:uppercase;letter-spacing:.03em;margin-bottom:2px}}
.stat-cell .val{{font-size:1.05rem;font-weight:700;color:#0f172a;font-variant-numeric:tabular-nums}}
.stat-cell .val.green{{color:#16a34a}}.stat-cell .val.red{{color:#dc2626}}.stat-cell .val.blue{{color:#2563eb}}

/* ── Tabs ──────────────────────────────────────────────── */
.tab-bar{{background:#fff;border-bottom:1px solid #e2e8f0;display:flex;gap:0;padding:0 32px;position:sticky;top:0;z-index:10}}
.tab{{padding:14px 20px;cursor:pointer;font-size:.8rem;font-weight:500;color:#64748b;border-bottom:2px solid transparent;transition:all .15s;user-select:none;white-space:nowrap}}
.tab:hover{{color:#0f172a}}.tab.active{{color:#7c3aed;border-bottom-color:#7c3aed;font-weight:600}}

/* ── Content ───────────────────────────────────────────── */
.content{{padding:24px 32px;max-width:1400px;margin:0 auto}}
.panel{{display:none}}.panel.active{{display:block}}
.section-title{{font-size:1.1rem;font-weight:700;color:#0f172a;margin:24px 0 12px;display:flex;align-items:center;gap:8px}}
.section-title:first-child{{margin-top:0}}

/* ── Chart Card ────────────────────────────────────────── */
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:20px;margin-bottom:20px}}
.card-title{{font-size:.85rem;font-weight:600;color:#334155;margin-bottom:12px}}

/* ── Tables ────────────────────────────────────────────── */
.tbl{{width:100%;border-collapse:collapse;font-size:.8rem;font-variant-numeric:tabular-nums}}
.tbl th{{background:#f8fafc;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.03em;font-size:.7rem;padding:10px 14px;text-align:left;border-bottom:2px solid #e2e8f0;position:sticky;top:0}}
.tbl td{{padding:8px 14px;border-bottom:1px solid #f1f5f9}}
.tbl td.val,.tbl th.val{{text-align:right}}
.tbl td.yr{{font-weight:600}}
.tbl tr:hover{{background:#f8fafc}}
.tbl .section-row{{background:#f1f5f9}}
.tbl .section-row td{{font-weight:700;font-size:.8rem;color:#475569;padding:12px 14px;border-bottom:2px solid #e2e8f0}}

/* ── Two Column Grid ───────────────────────────────────── */
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
@media(max-width:900px){{.grid-2{{grid-template-columns:1fr}}}}

/* ── Scrollable table wrapper ──────────────────────────── */
.tbl-wrap{{max-height:600px;overflow-y:auto;overflow-x:auto;border:1px solid #e2e8f0;border-radius:8px}}

/* ── Sorted hover tooltip ──────────────────────────────── */
.sorted-hover{{position:absolute;pointer-events:none;display:none;background:rgba(255,255,255,0.97);
  border:1px solid #e2e8f0;padding:8px 12px;font-size:.75rem;border-radius:6px;
  box-shadow:0 2px 8px rgba(0,0,0,.12);z-index:50;min-width:200px;max-width:320px}}

/* ── Info icon tooltip ─────────────────────────────────── */
.info-icon{{cursor:help;color:#94a3b8;font-size:.85em;position:relative;display:inline-block;margin-left:3px}}

/* ── Legend bulk toggle buttons ──────────────────────────── */
.legend-btn{{font-size:.7rem;font-weight:500;color:#475569;background:#fff;border:1px solid #cbd5e1;
  padding:3px 10px;border-radius:6px;cursor:pointer;transition:all .12s;font-family:inherit}}
.legend-btn:hover{{background:#f1f5f9;border-color:#94a3b8;color:#0f172a}}
.legend-btn:active{{background:#e2e8f0}}
.chart-tools{{margin-left:auto;display:flex;gap:6px;align-items:center}}
.info-icon:hover{{color:#6366f1}}
.info-icon:hover::after{{content:attr(title);position:absolute;bottom:120%;left:50%;transform:translateX(-50%);
  background:#1e293b;color:#f1f5f9;padding:4px 10px;border-radius:6px;font-size:.75rem;white-space:nowrap;
  z-index:20;pointer-events:none;box-shadow:0 2px 8px rgba(0,0,0,.15)}}
</style>
</head>
<body>

<!-- ═══ Header ═══ -->
<div class="hdr">
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    <h1>Results <span id="hdr-span">({years_span} years: {date_start} - {date_end})</span></h1>
    <div style="display:flex;align-items:center;gap:8px;margin-left:auto">
      <select id="quickRange" onchange="applyQuickRange()" style="padding:4px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:.8rem;color:#334155">
        <option value="">Quick Range</option>
        <option value="1m">Last 1 Month</option>
        <option value="3m">Last 3 Months</option>
        <option value="6m">Last 6 Months</option>
        <option value="ytd">Year to Date</option>
        <option value="1y">Last 1 Year</option>
        <option value="3y">Last 3 Years</option>
        <option value="5y">Last 5 Years</option>
        <option value="10y">Last 10 Years</option>
        <option value="all" selected>All Time</option>
      </select>
      <input type="date" id="dateFrom" value="{date_start}" style="padding:4px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:.8rem">
      <span style="color:#94a3b8">-</span>
      <input type="date" id="dateTo" value="{date_end}" style="padding:4px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:.8rem">
      <button onclick="applyDateRange()" style="padding:4px 12px;background:#7c3aed;color:#fff;border:none;border-radius:6px;font-size:.8rem;cursor:pointer">Apply</button>
    </div>
  </div>
  <div class="sub">
    <span class="badge badge-purple">Hedged TQQQ portfolios — BTAL / DBMF / DBBT bases × annual rebalance × optional monthly RV20 TQQQ↔QQQ swap</span>
  </div>
</div>

<!-- ═══ Comparison Statistics Table ═══ -->
<div style="padding:20px 32px 0">
<div class="section-title" style="margin-top:0">Statistics
  <span style="font-size:.75rem;font-weight:400;color:#64748b;margin-left:12px">Initial Investment: {fmt_dollar(start_eq)}</span>
</div>
<div class="tbl-wrap" style="max-height:none">
<table class="tbl" id="statsTable">
<thead><tr>
  <th>Name</th>
  <th class="val">Ending Value</th>
  <th class="val">Cumul. Return</th>
  <th class="val">CAGR</th>
  <th class="val">Max DD</th>
  <th class="val">Avg DD</th>
  <th class="val">Longest DD</th>
  <th class="val">Volatility</th>
  <th class="val">Sharpe</th>
  <th class="val">Sortino</th>
  <th class="val">Calmar</th>
  <th class="val">Ulcer Idx</th>
  <th class="val">UPI</th>
  <th class="val">Beta</th>
</tr></thead>
<tbody>
{stats_table_rows}
</tbody>
</tbody>
</table>
</div>
</div>

<!-- ═══ Tabs ═══ -->
<div class="tab-bar">
  <div class="tab active" onclick="showTab(0,this)">SUMMARY</div>
  <div class="tab" onclick="showTab(1,this)">METRICS</div>
  <div class="tab" onclick="showTab(2,this)">RETURNS</div>
  <div class="tab" onclick="showTab(3,this)">DRAWDOWN</div>
  <div class="tab" onclick="showTab(4,this)">ALLOCATION</div>
  <div class="tab" onclick="showTab(5,this)">REALLOCATION</div>
  <div class="tab" onclick="showTab(6,this)">REBALANCE LOG</div>
</div>

<div class="content">

<!-- ═══════════════════════════════════════════════════════
     SUMMARY
     ═══════════════════════════════════════════════════════ -->
<div class="panel active" id="p0">
  <div class="section-title">Performance
    <div class="chart-tools">
      <button class="legend-btn" onclick="toggleAllLegend('ch-equity', true)">Show all</button>
      <button class="legend-btn" onclick="toggleAllLegend('ch-equity', false)">Hide all</button>
      <label style="font-size:.75rem;font-weight:400;color:#64748b;cursor:pointer;display:flex;align-items:center;gap:4px;margin-left:6px">
        <input type="checkbox" id="logToggle" onchange="toggleLog()"> Logarithmic scale
      </label>
    </div>
  </div>
  <div class="card"><div id="ch-equity" style="height:600px"></div></div>

  <div class="section-title">Drawdown
    <div class="chart-tools">
      <button class="legend-btn" onclick="toggleAllLegend('ch-dd-summary', true)">Show all</button>
      <button class="legend-btn" onclick="toggleAllLegend('ch-dd-summary', false)">Hide all</button>
    </div>
  </div>
  <div class="card"><div id="ch-dd-summary" style="height:400px"></div></div>

  <div class="section-title">Annual Returns Bar Chart</div>
  <div class="card"><div id="ch-ann-bar" style="height:400px"></div></div>
</div>

<!-- ═══════════════════════════════════════════════════════
     METRICS
     ═══════════════════════════════════════════════════════ -->
<div class="panel" id="p1">
  <div class="section-title">Risk and Return Metrics</div>
  <div class="tbl-wrap"><table class="tbl">
    <thead><tr><th>Metric</th><th class="val">Strategy</th></tr></thead>
    <tbody>{build_metrics_html()}</tbody>
  </table></div>
</div>

<!-- ═══════════════════════════════════════════════════════
     RETURNS
     ═══════════════════════════════════════════════════════ -->
<div class="panel" id="p2">
  <div class="section-title">Annual Returns Histogram</div>
  <div class="card"><div id="ch-hist" style="height:300px"></div></div>

  <div class="grid-2">
    <div>
      <div class="section-title">Summary Statistics (Daily)</div>
      <div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Statistic</th><th class="val">Value</th></tr></thead>
        <tbody>{build_dist_table()}</tbody>
      </table></div>
    </div>
    <div>
      <div class="section-title">Annual Returns Table</div>
      <div class="tbl-wrap"><table class="tbl">
        <thead><tr>
          <th>Year</th><th class="val">Strategy</th><th class="val">Balance</th>
          <th class="val">SPY</th><th class="val">Balance</th>
        </tr></thead>
        <tbody>{build_annual_table_html()}</tbody>
      </table></div>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════════════════════════════
     DRAWDOWN
     ═══════════════════════════════════════════════════════ -->
<div class="panel" id="p3">
  <div class="section-title">Equity Drawdown
    <div class="chart-tools">
      <button class="legend-btn" onclick="toggleAllLegend('ch-dd-full', true)">Show all</button>
      <button class="legend-btn" onclick="toggleAllLegend('ch-dd-full', false)">Hide all</button>
    </div>
  </div>
  <div class="card"><div id="ch-dd-full" style="height:560px"></div></div>

  <div class="section-title">Underwater Plot (Rolling Returns %)</div>
  <div class="card"><div id="ch-rolling" style="height:300px"></div></div>
</div>

<!-- ═══════════════════════════════════════════════════════
     ALLOCATION
     ═══════════════════════════════════════════════════════ -->
<div class="panel" id="p4">
  <div class="section-title">Portfolio Allocation Over Time</div>
  <div class="card"><div id="ch-alloc" style="height:420px"></div></div>
</div>

<!-- ═══════════════════════════════════════════════════════
     REALLOCATION  (live "what to hold today" helper)
     ═══════════════════════════════════════════════════════ -->
<div class="panel" id="p-realloc">
  <div class="section-title">Reallocation — 按当前 RV20 计算建议持仓</div>
  <div class="card">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <label style="font-size:.9rem;color:#334155">计划持仓总金额 (USD)
        <input id="realloc-amount" type="number" min="0" step="1000" value="{int(_initial)}"
               oninput="renderRealloc()"
               style="margin-left:8px;padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;width:170px;font:inherit">
      </label>
    </div>
    <div id="realloc-error" style="display:none;margin-top:12px;background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;padding:12px 14px;border-radius:8px;font-size:.9rem;line-height:1.6"></div>
    <div id="realloc-status" style="font-size:.85rem;color:#475569;margin-top:12px"></div>
  </div>
  <div id="realloc-results" style="margin-top:14px"></div>
</div>

<!-- ═══════════════════════════════════════════════════════
     REBALANCE LOG
     ═══════════════════════════════════════════════════════ -->
<div class="panel" id="p5">
  {log_html}
</div>

</div><!-- /content -->

<!-- ═══════════════════════════════════════════════════════
     JAVASCRIPT
     ═══════════════════════════════════════════════════════ -->
<script>
const L = {{
  paper_bgcolor:'transparent', plot_bgcolor:'#fff',
  font:{{family:'Inter,system-ui,sans-serif',color:'#475569',size:12}},
  xaxis:{{gridcolor:'#f1f5f9',zeroline:false,linecolor:'#e2e8f0'}},
  yaxis:{{gridcolor:'#f1f5f9',zeroline:false,linecolor:'#e2e8f0',title:{{standoff:20}}}},
  legend:{{bgcolor:'transparent',bordercolor:'transparent',orientation:'h',y:1.20,yanchor:'top',x:0.5,xanchor:'center'}},
  margin:{{t:115,r:20,b:50,l:90}},
}};
// Layout for charts with custom sorted hover:
// hovermode:'x' fires plotly_hover with ALL traces at cursor x position.
// Native tooltips hidden via transparent hoverlabel + zero-size font.
// Spike line shows vertical crosshair.
const L_custom = {{...L,
  hovermode:false,
}};
// Layout for charts with native hover (bar charts etc.)
const L_native = {{...L, hovermode:'x unified', hoverlabel:{{bgcolor:'#fff',bordercolor:'#e2e8f0',font:{{color:'#1e293b'}}}}}};
const C = {{responsive:true,displayModeBar:false}};

/* ── Custom sorted hover tooltip (mousemove-based, no Plotly hover) ── */
function setupSortedHover(chartId) {{
  const chartDiv = document.getElementById(chartId);
  if (!chartDiv) return;
  let hoverDiv = chartDiv.parentElement.querySelector('.sorted-hover');
  if (!hoverDiv) {{
    hoverDiv = document.createElement('div');
    hoverDiv.className = 'sorted-hover';
    chartDiv.parentElement.style.position = 'relative';
    chartDiv.parentElement.appendChild(hoverDiv);
  }}
  // Spike line element
  let spikeLine = chartDiv.parentElement.querySelector('.spike-line');
  if (!spikeLine) {{
    spikeLine = document.createElement('div');
    spikeLine.className = 'spike-line';
    spikeLine.style.cssText = 'position:absolute;top:0;width:1px;background:#cbd5e1;pointer-events:none;display:none;z-index:5';
    chartDiv.parentElement.appendChild(spikeLine);
  }}

  function onMove(e) {{
    const layout = chartDiv._fullLayout;
    if (!layout || !layout.xaxis) return;
    const xa = layout.xaxis;

    // IMPORTANT: Plotly's xa.p2d / xa.r2p use PLOT-AREA-relative pixels
    // (0 = data start at xa._offset px from chart-container left, xa._length = data end).
    // Verified empirically: r2p('2019-05-08') = 0, r2p('2026-05-14') = xa._length.
    const chartRect = chartDiv.getBoundingClientRect();
    const xOff  = xa._offset || 0;
    const xLen  = xa._length || (chartRect.width - xOff - 20);
    const mouseInChart = e.clientX - chartRect.left;
    const mouseInPlot  = mouseInChart - xOff;               // 0 .. xLen for valid cursor
    if (mouseInPlot < 0 || mouseInPlot > xLen) {{
      hoverDiv.style.display='none'; spikeLine.style.display='none'; return;
    }}

    // Convert plot-area pixel to date.
    const xVal = xa.p2d(mouseInPlot);
    if (!xVal) {{ hoverDiv.style.display='none'; return; }}
    const dateStr = typeof xVal === 'string' ? xVal : xVal.substring?.(0,10) || '';

    // Find closest date index in trace data
    const traces = chartDiv.data;
    if (!traces || !traces.length) return;
    const xArr = traces[0].x;
    if (!xArr || !xArr.length) return;

    // Binary search for closest date
    let lo=0, hi=xArr.length-1, mid;
    while(lo<hi) {{ mid=(lo+hi)>>1; xArr[mid]<dateStr?lo=mid+1:hi=mid; }}
    const idx = lo;
    const closestDate = xArr[idx];

    // Collect values from all visible traces
    const items = [];
    for (let t = 0; t < traces.length; t++) {{
      if (traces[t].visible === 'legendonly' || traces[t].visible === false) continue;
      const y = traces[t].y[idx];
      if (y == null) continue;
      const color = traces[t].line?.color || traces[t].marker?.color || '#888';
      items.push({{ name: traces[t].name, y, color }});
    }}

    // Sort: equity (positive) descending, drawdown (negative) ascending
    const isDD = items.length > 0 && items[0].y <= 0;
    items.sort((a,b) => isDD ? a.y - b.y : b.y - a.y);

    let html = `<div style="font-weight:600;margin-bottom:4px">${{closestDate}}</div>`;
    for (const it of items) {{
      const val = isDD ? it.y.toFixed(2)+'%' : '$'+Math.round(it.y).toLocaleString();
      html += `<div style="display:flex;gap:6px;align-items:center">`
        +`<span style="color:${{it.color}};font-size:10px">&#9632;</span>`
        +`<span style="flex:1;color:#475569">${{it.name}}</span>`
        +`<span style="font-weight:600;font-variant-numeric:tabular-nums">${{val}}</span></div>`;
    }}
    hoverDiv.innerHTML = html;
    hoverDiv.style.display = 'block';

    const cRect = chartDiv.parentElement.getBoundingClientRect();
    let left = e.clientX - cRect.left + 15;
    if (left + 260 > cRect.width) left = e.clientX - cRect.left - 270;
    hoverDiv.style.left = left + 'px';
    hoverDiv.style.top = Math.max(0, e.clientY - cRect.top - 20) + 'px';

    // Spike line — anchor at the closest data point's pixel, span just the plot area.
    // xa.r2p returns plot-area-relative px, so add xa._offset for chart-container px.
    const pRect = chartDiv.parentElement.getBoundingClientRect();
    let pxInPlot;
    try {{
      pxInPlot = xa.r2p ? xa.r2p(closestDate)
                        : (typeof xa.d2c === 'function' ? xa.c2p(xa.d2c(closestDate)) : mouseInPlot);
    }} catch (err) {{ pxInPlot = mouseInPlot; }}
    const pxInChart = xOff + pxInPlot;
    const ya = layout.yaxis || {{}};
    spikeLine.style.left = ((chartRect.left - pRect.left) + pxInChart) + 'px';
    spikeLine.style.height = (ya._length || chartRect.height) + 'px';
    spikeLine.style.top = ((chartRect.top - pRect.top) + (ya._offset || 0)) + 'px';
    spikeLine.style.display = 'block';
  }}

  chartDiv.addEventListener('mousemove', onMove);
  chartDiv.addEventListener('mouseleave', () => {{
    hoverDiv.style.display = 'none';
    spikeLine.style.display = 'none';
  }});
}}

/* ── Shared data arrays (defined once, referenced everywhere) ── */
const D = {j(eq_dates)};
const V_strat = {j(eq_vals)};
const V_spy   = {j(comp_spy_eq)};
const V_qqq   = {j(comp_qqq_eq)};
const V_bt50  = {j(comp_bt50_eq)};
const DD_strat = {j(dd_strat)};
const DD_spy   = {j(dd_spy)};
const DD_qqq   = {j(dd_qqq)};
const DD_bt50  = {j(dd_bt50)};
const V_t1={j(comp_t1_eq)}, V_t1_25={j(comp_t1_25_eq)}, V_t1_20={j(comp_t1_20_eq)}, V_t1_rv60={j(comp_t1_rv60_eq)};
const V_t2={j(comp_t2_eq)}, V_t2_25={j(comp_t2_25_eq)}, V_t2_20={j(comp_t2_20_eq)};
const V_dbmf_strat={j(comp_dbmf_strat_eq)}, V_t1d_22={j(comp_t1d_22_eq)}, V_t1d_25={j(comp_t1d_25_eq)}, V_t1d_20={j(comp_t1d_20_eq)};
const V_dbbt_strat={j(comp_dbbt_strat_eq)}, V_t1b_22={j(comp_t1b_22_eq)}, V_t1b_25={j(comp_t1b_25_eq)}, V_t1b_20={j(comp_t1b_20_eq)};
const DD_t1={j(dd_t1)}, DD_t1_25={j(dd_t1_25)}, DD_t1_20={j(dd_t1_20)}, DD_t1_rv60={j(dd_t1_rv60)};
const DD_t2={j(dd_t2)}, DD_t2_25={j(dd_t2_25)}, DD_t2_20={j(dd_t2_20)};
const DD_dbmf_strat={j(dd_dbmf_strat)}, DD_t1d_22={j(dd_t1d_22)}, DD_t1d_25={j(dd_t1d_25)}, DD_t1d_20={j(dd_t1d_20)};
const DD_dbbt_strat={j(dd_dbbt_strat)}, DD_t1b_22={j(dd_t1b_22)}, DD_t1b_25={j(dd_t1b_25)}, DD_t1b_20={j(dd_t1b_20)};

/* ── DD annotation helpers ── */
function computeDDSpans(dates, vals, i0, i1) {{
  let peak=vals[i0], peakIdx=i0;
  let maxDD=0, maxPeakIdx=i0, maxTroughIdx=i0;
  let longestDays=0, longestStart=i0, longestEnd=i0;
  let curStart=i0, curDays=0;
  for(let i=i0;i<=i1;i++) {{
    if(vals[i]>=peak) {{ peak=vals[i]; peakIdx=i; curDays=0; curStart=i; }}
    const dd=(peak-vals[i])/peak;
    if(dd>0) {{ curDays++; if(curDays>longestDays){{longestDays=curDays;longestStart=curStart;longestEnd=i;}} }}
    if(dd>maxDD) {{ maxDD=dd; maxPeakIdx=peakIdx; maxTroughIdx=i; }}
  }}
  let maxRecov=i1;
  const pv=vals[maxPeakIdx];
  for(let i=maxTroughIdx+1;i<=i1;i++) if(vals[i]>=pv){{maxRecov=i;break;}}
  return {{
    maxDD:(maxDD*100).toFixed(2),
    maxPeakDate:dates[maxPeakIdx],maxTroughDate:dates[maxTroughIdx],maxRecovDate:dates[maxRecov],
    maxPeakVal:vals[maxPeakIdx],maxTroughVal:vals[maxTroughIdx],
    longestStartDate:dates[longestStart],longestEndDate:dates[longestEnd],
    longestYrs:(longestDays/252).toFixed(2),
  }};
}}

let _ddUpdating=false;
function updateDDAnnotations() {{
  if(_ddUpdating) return;
  _ddUpdating=true;
  // Use the CURRENT chart data of the MAIN strategy (DBBT-Base)
  const eqDiv=document.getElementById('ch-equity');
  if(!eqDiv||!eqDiv.data||!eqDiv.data.length) {{ _ddUpdating=false; return; }}
  const mainTrace = eqDiv.data.find(t => t.name === 'DBBT-Base') || eqDiv.data[0];
  const dates=mainTrace.x;
  const vals=mainTrace.y;
  const s=computeDDSpans(dates,vals,0,vals.length-1);
  Plotly.relayout('ch-equity',{{
    shapes:[
      {{type:'rect',xref:'x',yref:'paper',x0:s.maxPeakDate,x1:s.maxRecovDate,y0:0,y1:1,
        fillcolor:'rgba(239,68,68,0.08)',line:{{width:0}},layer:'below'}},
      {{type:'rect',xref:'x',yref:'paper',x0:s.longestStartDate,x1:s.longestEndDate,y0:0,y1:1,
        fillcolor:'rgba(59,130,246,0.06)',line:{{width:0}},layer:'below'}},
      {{type:'line',xref:'x',yref:'y',x0:s.maxPeakDate,y0:s.maxPeakVal,x1:s.maxTroughDate,y1:s.maxTroughVal,
        line:{{color:'#ef4444',width:2}}}},
    ],
    annotations:[
      {{x:s.maxTroughDate,y:s.maxTroughVal,xref:'x',yref:'y',
        text:'Max DD: -'+s.maxDD+'%',showarrow:true,arrowhead:2,arrowcolor:'#ef4444',
        font:{{size:11,color:'#dc2626'}},bgcolor:'#fff',bordercolor:'#fca5a5',borderpad:3,ax:40,ay:-30}},
      {{x:s.longestEndDate,y:0.02,xref:'x',yref:'paper',
        text:'Longest DD: '+s.longestYrs+' yrs',showarrow:false,
        font:{{size:10,color:'#2563eb'}},bgcolor:'#eff6ff',bordercolor:'#93c5fd',borderpad:3}},
    ]
  }}).then(()=>{{ _ddUpdating=false; }}).catch(()=>{{ _ddUpdating=false; }});
}}

function toggleLog(){{
  const isLog=document.getElementById('logToggle').checked;
  Plotly.relayout('ch-equity',{{'yaxis.type':isLog?'log':'linear'}});
}}

function toggleAllLegend(chartId, visible) {{
  const div = document.getElementById(chartId);
  if (!div || !div.data) return;
  const v = visible ? true : 'legendonly';
  const visArr = new Array(div.data.length).fill(v);
  Plotly.restyle(chartId, {{visible: visArr}});
}}

/* ── Native zoom → rebase to $100K at new start ──────────────
   When the user drags-to-zoom on a chart, Plotly only changes the visible
   range. We hook plotly_relayout to detect that and re-run recalcAll so
   every curve is rebased to $100K at the new range's start (matching what
   the Apply / Quick Range form does). _zoomRebasing guard prevents the
   relayout fired by our own Plotly.react inside recalcAll from re-entering. */
let _zoomRebasing = false;
function setupZoomRebase(chartId) {{
  const div = document.getElementById(chartId);
  if (!div || !div.on) return;
  div.on('plotly_relayout', (e) => {{
    if (_zoomRebasing) return;
    const r0 = e['xaxis.range[0]'];
    const r1 = e['xaxis.range[1]'];
    if (r0 === undefined || r1 === undefined) return;   // ignore non-user-zoom events
    const d0 = String(r0).slice(0, 10);
    const d1 = String(r1).slice(0, 10);
    document.getElementById('dateFrom').value = d0;
    document.getElementById('dateTo').value = d1;
    document.getElementById('quickRange').value = '';
    _zoomRebasing = true;
    try {{ recalcAll(d0, d1); }} finally {{
      // Release on next tick so Plotly.react's own relayout (autorange:true) finishes first
      setTimeout(() => {{ _zoomRebasing = false; }}, 0);
    }}
  }});
}}

/* ── Lazy chart rendering (only render when tab first shown) ── */
const rendered = new Set();
const ddRange = {_dd_range_json};

function renderChart(id) {{
  if(rendered.has(id)) return;
  rendered.add(id);

  if(id==='ch-equity') {{
    Plotly.newPlot('ch-equity',[
      {{x:D,y:V_spy,name:'100% SPY',type:'scatter',mode:'lines',line:{{color:'#f59e0b',width:1.5}}}},
      {{x:D,y:V_qqq,name:'100% QQQ',type:'scatter',mode:'lines',line:{{color:'#06b6d4',width:1.5}}}},
      {{x:D,y:V_bt50,name:'50%BTAL+50%TQQQ',type:'scatter',mode:'lines',line:{{color:'#22c55e',width:1.5}}}},
      {{x:D,y:V_strat,name:'BTAL-Base',type:'scatter',mode:'lines',line:{{color:'#7c3aed',width:2}}}},
      {{x:D,y:V_t1,name:'T1-BTAL-22%',type:'scatter',mode:'lines',line:{{color:'#d946ef',width:1.5}}}},
      {{x:D,y:V_t1_25,name:'T1-BTAL-25%',type:'scatter',mode:'lines',line:{{color:'#a855f7',width:1}}}},
      {{x:D,y:V_t1_20,name:'T1-BTAL-20%',type:'scatter',mode:'lines',line:{{color:'#ec4899',width:1}}}},
      {{x:D,y:V_t1_rv60,name:'T1-BTAL-RV60-22%',type:'scatter',mode:'lines',line:{{color:'#14b8a6',width:1.5}}}},
      {{x:D,y:V_t2,name:'T2-BTAL-22%',type:'scatter',mode:'lines',line:{{color:'#b45309',width:1.5}}}},
      {{x:D,y:V_t2_25,name:'T2-BTAL-25%',type:'scatter',mode:'lines',line:{{color:'#92400e',width:1}}}},
      {{x:D,y:V_t2_20,name:'T2-BTAL-20%',type:'scatter',mode:'lines',line:{{color:'#78350f',width:1}}}},
      {{x:D,y:V_dbmf_strat,name:'DBMF-Base',type:'scatter',mode:'lines',line:{{color:'#1e40af',width:2}}}},
      {{x:D,y:V_t1d_22,name:'T1-DBMF-22%',type:'scatter',mode:'lines',line:{{color:'#3b82f6',width:1.5}}}},
      {{x:D,y:V_t1d_25,name:'T1-DBMF-25%',type:'scatter',mode:'lines',line:{{color:'#0891b2',width:1.5}}}},
      {{x:D,y:V_t1d_20,name:'T1-DBMF-20%',type:'scatter',mode:'lines',line:{{color:'#0d9488',width:1.5}}}},
      {{x:D,y:V_dbbt_strat,name:'DBBT-Base',type:'scatter',mode:'lines',line:{{color:'#9f1239',width:2.5}}}},
      {{x:D,y:V_t1b_22,name:'T1-DBBT-22%',type:'scatter',mode:'lines',line:{{color:'#e11d48',width:1.5}}}},
      {{x:D,y:V_t1b_25,name:'T1-DBBT-25%',type:'scatter',mode:'lines',line:{{color:'#f43f5e',width:1.5}}}},
      {{x:D,y:V_t1b_20,name:'T1-DBBT-20%',type:'scatter',mode:'lines',line:{{color:'#fb7185',width:1.5}}}},
    ],{{...L_custom,yaxis:{{...L_custom.yaxis,title:'Portfolio Value ($)',tickformat:'$,.0f'}}}},C);
    setupSortedHover('ch-equity');
    setupZoomRebase('ch-equity');
    updateDDAnnotations();
  }}
  else if(id==='ch-dd-summary') {{
    Plotly.newPlot('ch-dd-summary',[
      {{x:D,y:DD_spy,name:'100% SPY',type:'scatter',mode:'lines',line:{{color:'#f59e0b',width:1}}}},
      {{x:D,y:DD_qqq,name:'100% QQQ',type:'scatter',mode:'lines',line:{{color:'#06b6d4',width:1}}}},
      {{x:D,y:DD_bt50,name:'50%BTAL+50%TQQQ',type:'scatter',mode:'lines',line:{{color:'#22c55e',width:1}}}},
      {{x:D,y:DD_strat,name:'BTAL-Base',type:'scatter',mode:'lines',line:{{color:'#7c3aed',width:1.5}}}},
      {{x:D,y:DD_t1,name:'T1-BTAL-22%',type:'scatter',mode:'lines',line:{{color:'#d946ef',width:1}}}},
      {{x:D,y:DD_t1_25,name:'T1-BTAL-25%',type:'scatter',mode:'lines',line:{{color:'#a855f7',width:1}}}},
      {{x:D,y:DD_t1_20,name:'T1-BTAL-20%',type:'scatter',mode:'lines',line:{{color:'#ec4899',width:1}}}},
      {{x:D,y:DD_t1_rv60,name:'T1-BTAL-RV60-22%',type:'scatter',mode:'lines',line:{{color:'#14b8a6',width:1}}}},
      {{x:D,y:DD_t2,name:'T2-BTAL-22%',type:'scatter',mode:'lines',line:{{color:'#b45309',width:1}}}},
      {{x:D,y:DD_t2_25,name:'T2-BTAL-25%',type:'scatter',mode:'lines',line:{{color:'#92400e',width:1}}}},
      {{x:D,y:DD_t2_20,name:'T2-BTAL-20%',type:'scatter',mode:'lines',line:{{color:'#78350f',width:1}}}},
      {{x:D,y:DD_dbmf_strat,name:'DBMF-Base',type:'scatter',mode:'lines',line:{{color:'#1e40af',width:1.5}}}},
      {{x:D,y:DD_t1d_22,name:'T1-DBMF-22%',type:'scatter',mode:'lines',line:{{color:'#3b82f6',width:1}}}},
      {{x:D,y:DD_t1d_25,name:'T1-DBMF-25%',type:'scatter',mode:'lines',line:{{color:'#0891b2',width:1}}}},
      {{x:D,y:DD_t1d_20,name:'T1-DBMF-20%',type:'scatter',mode:'lines',line:{{color:'#0d9488',width:1}}}},
      {{x:D,y:DD_dbbt_strat,name:'DBBT-Base',type:'scatter',mode:'lines',line:{{color:'#9f1239',width:2}},fill:'tozeroy',fillcolor:'rgba(159,18,57,0.08)'}},
      {{x:D,y:DD_t1b_22,name:'T1-DBBT-22%',type:'scatter',mode:'lines',line:{{color:'#e11d48',width:1}}}},
      {{x:D,y:DD_t1b_25,name:'T1-DBBT-25%',type:'scatter',mode:'lines',line:{{color:'#f43f5e',width:1}}}},
      {{x:D,y:DD_t1b_20,name:'T1-DBBT-20%',type:'scatter',mode:'lines',line:{{color:'#fb7185',width:1}}}},
    ],{{...L_custom,yaxis:{{...L_custom.yaxis,title:'Drawdown',tickformat:'.1f',ticksuffix:'%',range:ddRange}},margin:{{t:115,r:20,b:40,l:90}}}},C);
    setupSortedHover('ch-dd-summary');
    setupZoomRebase('ch-dd-summary');
  }}
  else if(id==='ch-ann-bar') {{
    Plotly.newPlot('ch-ann-bar',[
      {{x:{j(ann_years)},y:{j(comp_spy_ann)},name:'100% SPY',type:'bar',marker:{{color:'#f59e0b'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_qqq_ann)},name:'100% QQQ',type:'bar',marker:{{color:'#06b6d4'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_bt50_ann)},name:'50%BTAL+50%TQQQ',type:'bar',marker:{{color:'#22c55e'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(ann_strat)},name:'BTAL-Base',type:'bar',marker:{{color:'#7c3aed'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_t1_25_ann)},name:'T1-BTAL-25%',type:'bar',marker:{{color:'#a855f7'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_t1_ann)},name:'T1-BTAL-22%',type:'bar',marker:{{color:'#d946ef'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_t1_20_ann)},name:'T1-BTAL-20%',type:'bar',marker:{{color:'#ec4899'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_t1_rv60_ann)},name:'T1-BTAL-RV60-22%',type:'bar',marker:{{color:'#14b8a6'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_t2_25_ann)},name:'T2-BTAL-25%',type:'bar',marker:{{color:'#92400e'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_t2_ann)},name:'T2-BTAL-22%',type:'bar',marker:{{color:'#b45309'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_t2_20_ann)},name:'T2-BTAL-20%',type:'bar',marker:{{color:'#78350f'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_dbmf_strat_ann)},name:'DBMF-Base',type:'bar',marker:{{color:'#1e40af'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_t1d_25_ann)},name:'T1-DBMF-25%',type:'bar',marker:{{color:'#0891b2'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_t1d_22_ann)},name:'T1-DBMF-22%',type:'bar',marker:{{color:'#3b82f6'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_t1d_20_ann)},name:'T1-DBMF-20%',type:'bar',marker:{{color:'#0d9488'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_dbbt_strat_ann)},name:'DBBT-Base',type:'bar',marker:{{color:'#9f1239'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_t1b_25_ann)},name:'T1-DBBT-25%',type:'bar',marker:{{color:'#f43f5e'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>'}},
      {{x:{j(ann_years)},y:{j(comp_t1b_22_ann)},name:'T1-DBBT-22%',type:'bar',marker:{{color:'#e11d48'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
      {{x:{j(ann_years)},y:{j(comp_t1b_20_ann)},name:'T1-DBBT-20%',type:'bar',marker:{{color:'#fb7185'}},hovertemplate:'%{{y:.1f}}%<extra>%{{fullData.name}}</extra>',visible:'legendonly'}},
    ],{{...L_native,barmode:'group',yaxis:{{...L_native.yaxis,title:'Return (%)',ticksuffix:'%'}},margin:{{t:115,r:20,b:50,l:90}}}},C);
  }}
  else if(id==='ch-hist') {{
    Plotly.newPlot('ch-hist',[
      {{x:{j(ann_strat)},type:'histogram',name:'BTAL-Base',marker:{{color:'#7c3aed'}},xbins:{{size:5}},opacity:0.8}},
      {{x:{j(ann_bm)},type:'histogram',name:'S&P 500',marker:{{color:'#f59e0b'}},xbins:{{size:5}},opacity:0.6}},
    ],{{...L_native,barmode:'overlay',xaxis:{{...L_native.xaxis,title:'Annual Return (%)'}},yaxis:{{...L_native.yaxis,title:'Frequency'}}}},C);
  }}
  else if(id==='ch-dd-full') {{
    Plotly.newPlot('ch-dd-full',[
      {{x:D,y:DD_spy,name:'100% SPY',type:'scatter',mode:'lines',line:{{color:'#f59e0b',width:1}}}},
      {{x:D,y:DD_qqq,name:'100% QQQ',type:'scatter',mode:'lines',line:{{color:'#06b6d4',width:1}}}},
      {{x:D,y:DD_bt50,name:'50%BTAL+50%TQQQ',type:'scatter',mode:'lines',line:{{color:'#22c55e',width:1}}}},
      {{x:D,y:DD_strat,name:'BTAL-Base',type:'scatter',mode:'lines',line:{{color:'#7c3aed',width:1.5}}}},
      {{x:D,y:DD_t1,name:'T1-BTAL-22%',type:'scatter',mode:'lines',line:{{color:'#d946ef',width:1}}}},
      {{x:D,y:DD_t1_25,name:'T1-BTAL-25%',type:'scatter',mode:'lines',line:{{color:'#a855f7',width:1}}}},
      {{x:D,y:DD_t1_20,name:'T1-BTAL-20%',type:'scatter',mode:'lines',line:{{color:'#ec4899',width:1}}}},
      {{x:D,y:DD_t1_rv60,name:'T1-BTAL-RV60-22%',type:'scatter',mode:'lines',line:{{color:'#14b8a6',width:1}}}},
      {{x:D,y:DD_t2,name:'T2-BTAL-22%',type:'scatter',mode:'lines',line:{{color:'#b45309',width:1}}}},
      {{x:D,y:DD_t2_25,name:'T2-BTAL-25%',type:'scatter',mode:'lines',line:{{color:'#92400e',width:1}}}},
      {{x:D,y:DD_t2_20,name:'T2-BTAL-20%',type:'scatter',mode:'lines',line:{{color:'#78350f',width:1}}}},
      {{x:D,y:DD_dbmf_strat,name:'DBMF-Base',type:'scatter',mode:'lines',line:{{color:'#1e40af',width:1.5}}}},
      {{x:D,y:DD_t1d_22,name:'T1-DBMF-22%',type:'scatter',mode:'lines',line:{{color:'#3b82f6',width:1}}}},
      {{x:D,y:DD_t1d_25,name:'T1-DBMF-25%',type:'scatter',mode:'lines',line:{{color:'#0891b2',width:1}}}},
      {{x:D,y:DD_t1d_20,name:'T1-DBMF-20%',type:'scatter',mode:'lines',line:{{color:'#0d9488',width:1}}}},
      {{x:D,y:DD_dbbt_strat,name:'DBBT-Base',type:'scatter',mode:'lines',line:{{color:'#9f1239',width:2}},fill:'tozeroy',fillcolor:'rgba(159,18,57,0.08)'}},
      {{x:D,y:DD_t1b_22,name:'T1-DBBT-22%',type:'scatter',mode:'lines',line:{{color:'#e11d48',width:1}}}},
      {{x:D,y:DD_t1b_25,name:'T1-DBBT-25%',type:'scatter',mode:'lines',line:{{color:'#f43f5e',width:1}}}},
      {{x:D,y:DD_t1b_20,name:'T1-DBBT-20%',type:'scatter',mode:'lines',line:{{color:'#fb7185',width:1}}}},
    ],{{...L_custom,yaxis:{{...L_custom.yaxis,title:'Drawdown',tickformat:'.1f',ticksuffix:'%',range:ddRange}}}},C);
    setupSortedHover('ch-dd-full');
    setupZoomRebase('ch-dd-full');
  }}
  else if(id==='ch-rolling') {{
    const ma=[];
    for(let i=0;i<retVals.length;i++){{
      const s=Math.max(0,i-29);
      const sl=retVals.slice(s,i+1);
      ma.push(sl.reduce((a,b)=>a+b,0)/sl.length);
    }}
    Plotly.newPlot('ch-rolling',[
      {{x:retDates,y:ma,name:'30-day Avg Return',type:'scatter',mode:'lines',
        line:{{color:'#7c3aed',width:1.5}},fill:'tozeroy',fillcolor:'rgba(124,58,237,0.05)'}},
    ],{{...L,yaxis:{{...L.yaxis,title:'Avg Daily Return (%)',ticksuffix:'%'}},showlegend:false}},C);
  }}
  else if(id==='ch-alloc') {{
    Plotly.newPlot('ch-alloc',[{margin_traces_js}],
      {{...L,yaxis:{{...L.yaxis,title:'Weight (%)',ticksuffix:'%',range:[0,105]}}}},C);
  }}
}}

/* ── Chart IDs per tab panel ── */
const tabCharts = {{
  0: ['ch-equity','ch-dd-summary','ch-ann-bar'],
  1: [],
  2: ['ch-hist'],
  3: ['ch-dd-full','ch-rolling'],
  4: ['ch-alloc'],
}};

const retDates={j(ret_dates)};
const retVals={j(ret_vals)};

/* ── Tab switching with lazy render ── */
const panels=document.querySelectorAll('.panel');
function showTab(idx,el){{
  panels.forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  panels[idx].classList.add('active');
  el.classList.add('active');
  // Lazy render charts for this tab
  (tabCharts[idx]||[]).forEach(id=>{{
    renderChart(id);
    try{{Plotly.Plots.resize(id)}}catch(e){{}}
  }});
}}

// Render initial tab (Summary) on load
requestAnimationFrame(()=>{{ (tabCharts[0]||[]).forEach(renderChart); }});

/* ═══════════════════════════════════════════════════════════════
   TIME RANGE SELECTOR — client-side metric recalculation
   ═══════════════════════════════════════════════════════════════ */

const ALL_D = D;  // full date array reference
const PORTFOLIOS = [
  // Benchmarks first
  {{name:'100% SPY',color:'#f59e0b',eq:V_spy,dd:DD_spy,bg:'',dr:1}},
  {{name:'100% QQQ',color:'#06b6d4',eq:V_qqq,dd:DD_qqq,bg:'',dr:1}},
  {{name:'50%BTAL+50%TQQQ',color:'#22c55e',eq:V_bt50,dd:DD_bt50,bg:'',dr:{f'{dr_bt50:.2f}'} }},
  // BTAL family
  {{name:'35tqqq+30btal+15gld+15xlp+5cure',color:'#7c3aed',eq:V_strat,dd:DD_strat,bg:'#f5f3ff',dr:{f'{dr_strat:.2f}'} }},
  {{name:'Timing1-BTAL-RV20-22%',color:'#d946ef',eq:V_t1,dd:DD_t1,bg:'#fdf4ff',dr:0}},
  {{name:'Timing1-BTAL-RV20-25%',color:'#a855f7',eq:V_t1_25,dd:DD_t1_25,bg:'#faf5ff',dr:0}},
  {{name:'Timing1-BTAL-RV20-20%',color:'#ec4899',eq:V_t1_20,dd:DD_t1_20,bg:'#fdf2f8',dr:0}},
  {{name:'Timing1-BTAL-RV60-22%',color:'#14b8a6',eq:V_t1_rv60,dd:DD_t1_rv60,bg:'#f0fdfa',dr:0}},
  {{name:'Timing2-BTAL-RV20-22%',color:'#b45309',eq:V_t2,dd:DD_t2,bg:'#fef3c7',dr:0}},
  {{name:'Timing2-BTAL-RV20-25%',color:'#92400e',eq:V_t2_25,dd:DD_t2_25,bg:'#fefce8',dr:0}},
  {{name:'Timing2-BTAL-RV20-20%',color:'#78350f',eq:V_t2_20,dd:DD_t2_20,bg:'#fffbeb',dr:0}},
  // DBMF family
  {{name:'25tqqq+40dbmf+15gld+15xlp+5cure',color:'#1e40af',eq:V_dbmf_strat,dd:DD_dbmf_strat,bg:'#dbeafe',dr:{f'{dr_dbmf_strat:.2f}'} }},
  {{name:'Timing1-DBMF-RV20-22%',color:'#3b82f6',eq:V_t1d_22,dd:DD_t1d_22,bg:'#eff6ff',dr:0}},
  {{name:'Timing1-DBMF-RV20-25%',color:'#0891b2',eq:V_t1d_25,dd:DD_t1d_25,bg:'#cffafe',dr:0}},
  {{name:'Timing1-DBMF-RV20-20%',color:'#0d9488',eq:V_t1d_20,dd:DD_t1d_20,bg:'#ccfbf1',dr:0}},
  // DBBT family
  {{name:'30tqqq+20dbmf+15btal+15gld+15xlp+5cure',color:'#9f1239',eq:V_dbbt_strat,dd:DD_dbbt_strat,bg:'#ffe4e6',dr:{f'{dr_dbbt_strat:.2f}'} }},
  {{name:'Timing1-DBBT-RV20-22%',color:'#e11d48',eq:V_t1b_22,dd:DD_t1b_22,bg:'#fff1f2',dr:0}},
  {{name:'Timing1-DBBT-RV20-25%',color:'#f43f5e',eq:V_t1b_25,dd:DD_t1b_25,bg:'#ffe4e6',dr:0}},
  {{name:'Timing1-DBBT-RV20-20%',color:'#fb7185',eq:V_t1b_20,dd:DD_t1b_20,bg:'#fff1f2',dr:0}},
];
const SPY_IDX = PORTFOLIOS.findIndex(p=>p.name==='100% SPY');

function csRange(d0, d1) {{
  // Find index range in ALL_D for [d0, d1]
  let i0=0, i1=ALL_D.length-1;
  if(d0) for(let i=0;i<ALL_D.length;i++) if(ALL_D[i]>=d0){{i0=i;break;}}
  if(d1) for(let i=ALL_D.length-1;i>=0;i--) if(ALL_D[i]<=d1){{i1=i;break;}}
  return [i0,i1];
}}

function csStats(eq, i0, i1) {{
  const n=i1-i0;
  if(n<2) return {{}};
  const sv=eq[i0], ev=eq[i1];
  const cumRet=(ev/sv-1)*100;
  // Parse dates for years
  const d0=new Date(ALL_D[i0]), d1=new Date(ALL_D[i1]);
  const yrs=(d1-d0)/(365.25*86400000);
  const cagr=yrs>0?((ev/sv)**(1/yrs)-1)*100:0;

  // Daily returns (skip zero-return carry-forward days)
  const rets=[];
  for(let i=i0+1;i<=i1;i++){{
    if(eq[i-1]>0){{
      const r=eq[i]/eq[i-1]-1;
      if(r===0&&eq[i]===eq[i-1]) continue;
      rets.push(r);
    }}
  }}
  const nr=rets.length||1;
  const meanR=rets.reduce((a,b)=>a+b,0)/nr;
  const stdR=Math.sqrt(rets.reduce((a,r)=>a+(r-meanR)**2,0)/nr);
  const vol=stdR*Math.sqrt(252)*100;
  const sharpe=stdR>0?(meanR/stdR)*Math.sqrt(252):0;
  const dsSq=rets.reduce((a,r)=>a+Math.min(r,0)**2,0);
  const dsDev=Math.sqrt(dsSq/nr);
  const sortino=dsDev>0?(meanR/dsDev)*Math.sqrt(252):0;

  // Drawdown with date tracking
  let peak=eq[i0], peakIdx=i0, maxDD=0, maxDDpeakIdx=i0, maxDDtroughIdx=i0;
  let ddDays=0, longestDD=0, longestStart=i0, longestEnd=i0, curStart=i0;
  let ddSum=0, ddCount=0;
  for(let i=i0;i<=i1;i++){{
    if(eq[i]>=peak){{peak=eq[i];peakIdx=i;ddDays=0;curStart=i;}}
    const dd=(eq[i]-peak)/peak*100;
    if(dd<0){{
      ddDays++;ddSum+=Math.abs(dd);ddCount++;
      if(ddDays>longestDD){{longestDD=ddDays;longestStart=curStart;longestEnd=i;}}
    }}
    if(Math.abs(dd)>maxDD){{maxDD=Math.abs(dd);maxDDpeakIdx=peakIdx;maxDDtroughIdx=i;}}
  }}
  const avgDD=ddCount>0?ddSum/ddCount:0;
  const longestYrs=(longestDD/252);
  const calmar=maxDD>0?Math.abs(cagr)/maxDD:0;

  // Ulcer
  let ddSqAll=0;
  peak=eq[i0];
  for(let i=i0;i<=i1;i++){{
    if(eq[i]>=peak)peak=eq[i];
    const dd=(eq[i]-peak)/peak*100;
    ddSqAll+=dd*dd;
  }}
  const ulcer=Math.sqrt(ddSqAll/(i1-i0+1));
  const upi=ulcer>0?cagr/ulcer:0;

  return {{endVal:ev,cumRet,cagr,maxDD,avgDD,longestYrs,vol,sharpe,sortino,calmar,ulcer,upi,
    maxDDstart:ALL_D[maxDDpeakIdx]||'',maxDDend:ALL_D[maxDDtroughIdx]||'',
    longestStart:ALL_D[longestStart]||'',longestEnd:ALL_D[longestEnd]||''}};
}}

function csBeta(eq, bench, i0, i1) {{
  const ra=[], rb=[];
  for(let i=i0+1;i<=i1;i++){{
    if(eq[i-1]>0&&bench[i-1]>0){{
      const a=eq[i]/eq[i-1]-1, b=bench[i]/bench[i-1]-1;
      if(a===0&&b===0) continue;
      ra.push(a); rb.push(b);
    }}
  }}
  if(rb.length<2) return 0;
  const n=rb.length;
  const mb=rb.reduce((a,b)=>a+b,0)/n;
  const ma=ra.reduce((a,b)=>a+b,0)/n;
  const cov=ra.reduce((s,a,i)=>s+(a-ma)*(rb[i]-mb),0)/n;
  const varB=rb.reduce((s,b)=>s+(b-mb)**2,0)/n;
  return varB>0?cov/varB:0;
}}

function fmtD(v){{return '$'+v.toLocaleString('en-US',{{maximumFractionDigits:0}})}}
function fmtP(v,d=2){{return v.toFixed(d)+'%'}}

function rebaseEquity(eq, i0, i1, initial) {{
  // Rebase equity curve so eq[i0] = initial, preserving daily returns
  const scale = initial / (eq[i0] || 1);
  const out = [];
  for (let i = i0; i <= i1; i++) out.push(eq[i] * scale);
  return out;
}}

function rebaseDD(eq, i0, i1) {{
  // Compute drawdown series (%) from equity slice, 0% at top
  let peak = eq[i0];
  const out = [];
  for (let i = i0; i <= i1; i++) {{
    if (eq[i] >= peak) peak = eq[i];
    out.push(peak > 0 ? (eq[i] - peak) / peak * 100 : 0);
  }}
  return out;
}}

function recalcAll(d0, d1) {{
  const [i0, i1] = csRange(d0, d1);
  const dates = ALL_D.slice(i0, i1 + 1);
  const spyEq = PORTFOLIOS[SPY_IDX].eq;
  const initial = 100000;

  // Update header span
  const yrs = (new Date(ALL_D[i1]) - new Date(ALL_D[i0])) / (365.25 * 86400000);
  document.getElementById('hdr-span').textContent =
    '(' + yrs.toFixed(2) + ' years: ' + ALL_D[i0] + ' - ' + ALL_D[i1] + ')';

  // ── Rebase all equity curves to $100K at new start ──
  const rebased = PORTFOLIOS.map(p => rebaseEquity(p.eq, i0, i1, initial));
  const rebasedDD = PORTFOLIOS.map(p => rebaseDD(p.eq, i0, i1));

  // ── Update stats table (use REBASED equity for endVal display) ──
  const tbody = document.querySelector('#statsTable tbody');
  if (!tbody) return;

  let html = '';
  PORTFOLIOS.forEach((p, idx) => {{
    const s = csStats(p.eq, i0, i1);
    // Override endVal with rebased final value
    s.endVal = rebased[idx][rebased[idx].length - 1];
    const beta = idx === SPY_IDX ? 1 : csBeta(p.eq, spyEq, i0, i1);
    const bg = p.bg ? `style="background:${{p.bg}}"` : '';
    html += `<tr ${{bg}}>
      <td style="font-weight:600;color:${{p.color}}">${{p.name}}</td>
      <td class="val">${{fmtD(s.endVal || 0)}}</td>
      <td class="val">${{fmtP(s.cumRet || 0)}}</td>
      <td class="val">${{fmtP(s.cagr || 0)}}</td>
      <td class="val" title="${{s.maxDDstart||''}} to ${{s.maxDDend||''}}" style="cursor:help">-${{fmtP(s.maxDD || 0)}}</td>
      <td class="val">-${{fmtP(s.avgDD || 0)}}</td>
      <td class="val" title="${{s.longestStart||''}} to ${{s.longestEnd||''}}" style="cursor:help">${{(s.longestYrs || 0).toFixed(2)}} yrs</td>
      <td class="val">${{fmtP(s.vol || 0)}}</td>
      <td class="val">${{(s.sharpe || 0).toFixed(2)}}</td>
      <td class="val">${{(s.sortino || 0).toFixed(2)}}</td>
      <td class="val">${{(s.calmar || 0).toFixed(2)}}</td>
      <td class="val">${{(s.ulcer || 0).toFixed(2)}}</td>
      <td class="val">${{(s.upi || 0).toFixed(2)}}</td>
      <td class="val">${{beta.toFixed(2)}}</td>
    </tr>`;
  }});
  tbody.innerHTML = html;

  // ── Rebuild Performance chart with rebased data ──
  if (rendered.has('ch-equity')) {{
    const eqDiv = document.getElementById('ch-equity');
    const traces = eqDiv.data;
    for (let t = 0; t < Math.min(traces.length, PORTFOLIOS.length); t++) {{
      traces[t].x = dates;
      traces[t].y = rebased[t];
    }}
    const isLog = document.getElementById('logToggle').checked;
    const nl = JSON.parse(JSON.stringify(eqDiv.layout));
    nl.yaxis.autorange = true;
    nl.yaxis.type = isLog ? 'log' : 'linear';
    nl.xaxis.autorange = true;
    delete nl.shapes; delete nl.annotations;
    Plotly.react('ch-equity', traces, nl);
    _ddUpdating = false;
    updateDDAnnotations();
  }}

  // ── Rebuild Drawdown charts with rebased DD ──
  function updateDDChart(chartId) {{
    if (!rendered.has(chartId)) return;
    const div = document.getElementById(chartId);
    const traces = div.data;
    for (let t = 0; t < Math.min(traces.length, PORTFOLIOS.length); t++) {{
      traces[t].x = dates;
      traces[t].y = rebasedDD[t];
    }}
    let minDD = 0;
    rebasedDD.forEach(dd => {{ for(let i=0;i<dd.length;i++) if(dd[i]<minDD) minDD=dd[i]; }});
    const nl = JSON.parse(JSON.stringify(div.layout));
    nl.yaxis.range = [minDD * 1.05, 2];
    nl.xaxis.autorange = true;
    Plotly.react(chartId, traces, nl);
  }}
  updateDDChart('ch-dd-summary');
  updateDDChart('ch-dd-full');
}}

function applyDateRange() {{
  const d0=document.getElementById('dateFrom').value;
  const d1=document.getElementById('dateTo').value;
  document.getElementById('quickRange').value='';
  recalcAll(d0,d1);
}}

function applyQuickRange() {{
  const v=document.getElementById('quickRange').value;
  if(!v) return;
  const endDate=ALL_D[ALL_D.length-1];
  const end=new Date(endDate);
  let start;
  if(v==='all'){{
    document.getElementById('dateFrom').value=ALL_D[0];
    document.getElementById('dateTo').value=endDate;
    recalcAll(null,null);
    return;
  }}
  else if(v==='ytd') start=new Date(end.getFullYear(),0,1);
  else if(v==='1m'){{start=new Date(end);start.setMonth(start.getMonth()-1);}}
  else if(v==='3m'){{start=new Date(end);start.setMonth(start.getMonth()-3);}}
  else if(v==='6m'){{start=new Date(end);start.setMonth(start.getMonth()-6);}}
  else if(v==='1y'){{start=new Date(end);start.setFullYear(start.getFullYear()-1);}}
  else if(v==='3y'){{start=new Date(end);start.setFullYear(start.getFullYear()-3);}}
  else if(v==='5y'){{start=new Date(end);start.setFullYear(start.getFullYear()-5);}}
  else if(v==='10y'){{start=new Date(end);start.setFullYear(start.getFullYear()-10);}}
  const d0=start.toISOString().slice(0,10);
  document.getElementById('dateFrom').value=d0;
  document.getElementById('dateTo').value=endDate;
  recalcAll(d0,endDate);
}}

/* ── Reallocation tab (live holdings helper) ── */
{_realloc_js}
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
output_html.write_text(html, encoding="utf-8")
print(f"Report written to: {output_html}")
print(f"Open in browser:   open '{output_html}'")
