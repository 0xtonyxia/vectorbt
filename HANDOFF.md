# Session Handoff — Portfolio Backtest Project

**Repo**: this is a fork of [polakowo/vectorbt](https://github.com/polakowo/vectorbt) under `0xtonyxia/vectorbt`. We use vectorbt as a library AND host our strategy backtest code in a `strategies/` directory inside this same repo. Original upstream is preserved as `upstream` remote.

**Branch**: `us-market-strategy-bt`

**Last verified commit**: see `git log --oneline -10`

---

## 1. What this project does

A portfolio backtest framework for a **leveraged + hedged ETF strategy** plus several **timing variants**. Started life on QuantConnect's Lean (in `~/work/Lean`), then was migrated to pure Python in this repo because Lean was overkill for our use case.

The deliverable is a **single interactive HTML report** with:
- Stats table comparing 11 portfolios side-by-side (CAGR, Max DD, Sharpe, Sortino, Calmar, Ulcer, UPI, Beta, etc.)
- Performance / Drawdown / Returns / Allocation / Rebalance Log tabs
- Time range selector with client-side recompute (selecting "Last 1Y" rebases everything to $100K at the new start and recomputes all metrics in JS)
- Sorted hover tooltip (cursor anywhere on the chart → see all 11 values at that x, sorted high→low)
- Max DD / Longest DD annotations that follow zoom

---

## 2. The strategies

### Base: `35% TQQQ + 30% BTAL + 15% GLD + 15% XLP + 5% CURE`
- **Annual rebalance** on first trading day of each calendar year
- TQQQ: 3x leveraged QQQ (growth engine)
- BTAL: anti-beta long/short hedge (tail-risk damper)
- GLD: gold (inflation / crisis hedge)
- XLP: consumer staples ETF (defensive)
- CURE: 3x leveraged healthcare (niche growth)

### Timing1 variants (4 of them)
On the **first trading day of each month** (or quarter), check QQQ's realized vol; swap the 35% attack leg between TQQQ and QQQ:
- If `RV > threshold` and currently TQQQ → swap all to QQQ
- If `RV ≤ threshold` and currently QQQ → swap back to TQQQ
- **Fixed legs (BTAL/GLD/XLP/CURE) drift freely** between annual rebalances — only TQQQ↔QQQ position gets swapped on timing checks.

| Variant | RV window | Threshold | Check freq |
|---|---|---|---|
| `Timing1-RV20-22%` | 20 days | 22% | monthly |
| `Timing1-RV20-25%` | 20 days | 25% | monthly |
| `Timing1-RV20-20%` | 20 days | 20% | monthly |
| `Timing1-RV60-22%` | 60 days | 22% | quarterly |

### Timing2 variants (3 of them)
Same monthly check, but instead of all-or-nothing swap:
- If `RV20 > threshold`: target attack leg = **25% TQQQ + 75% QQQ**
- Else: target attack leg = **100% TQQQ**

| Variant | Threshold |
|---|---|
| `Timing2-RV20-22%` | 22% |
| `Timing2-RV20-25%` | 25% |
| `Timing2-RV20-20%` | 20% |

### Comparison benchmarks
- `100% SPY` (buy-and-hold)
- `100% QQQ` (buy-and-hold)
- `50% BTAL + 50% TQQQ` (annual rebalance)

**Result for 14.6-year backtest (2011-09-14 → 2026-04-06)**:
- Base strategy: $100K → **$1.99M** (CAGR 22.78%, Max DD -32.52%)
- Best variant: `Timing1-RV20-25%`: CAGR 23.51%, Max DD **-21.20%** (much better Calmar)

---

## 3. Repo structure

```
~/work/vectorbt/
├── vectorbt/ tests/ docs/ ...    # upstream vectorbt library (untouched)
├── HANDOFF.md                    # THIS FILE
│
├── Data/equity/usa/daily/        # Daily OHLCV zips (Lean format: yyyyMMdd HH:mm,O,H,L,C,V *10000)
│   ├── tqqq.zip  btal.zip  gld.zip  xlp.zip  cure.zip
│   └── spy.zip   qqq.zip   bil.zip
│
├── strategies/
│   └── tqqq_diversified/         # Current strategy (named: TQQQ-centric diversified)
│       ├── README.md
│       ├── sim_engine.py         # Simulation + metrics engine. Pluggable backend.
│       ├── run_backtest.py       # Driver. Writes Lean-shaped JSON + invokes report.
│       ├── generate_report.py    # Generates interactive HTML from JSON.
│       └── download_data.py      # yfinance → Lean-format daily zips.
│
└── output/<strategy>-<ts>/       # Per-run output (gitignored)
    ├── TQQQPortfolioStrategy.json
    ├── TQQQPortfolioStrategy.html
    └── TQQQPortfolioStrategy-log.txt
```

**Strategy naming convention**: each strategy lives in `strategies/<strategy_name>/`. To add a new strategy, copy `tqqq_diversified/` and adapt. The 4 files in the directory are self-contained — no shared modules across strategies yet (refactor only when a second strategy actually exists and duplication becomes painful).

---

## 4. How to run

```bash
cd ~/work/vectorbt

# 1. Refresh price data (incremental, only fetches new days since last run)
python3.11 strategies/tqqq_diversified/download_data.py

# 2. Full backtest, default range 2011-09-14 → 2026-04-06, auto-opens HTML
python3.11 strategies/tqqq_diversified/run_backtest.py

# Useful flags:
#   --start YYYY-MM-DD       custom backtest start
#   --end   YYYY-MM-DD       custom backtest end
#   --no-open                don't auto-open browser
#   --no-report              skip HTML generation
#   --backend python         (default) ~0.4s end-to-end
#   --backend vectorbt       ~4.4s end-to-end (Numba JIT overhead at our scale)
```

---

## 5. Key technical decisions

### 5.1 Lean was removed
Originally we ran the base strategy as a Lean `QCAlgorithm`, but:
- 99% of the actual computation already happened in our Python `simulate_*` functions inside `generate_report.py` (because comparison portfolios aren't Lean backtests, and Lean's equity curve had a 1-day UTC timing artifact that broke Beta calculation)
- dotnet build/startup added ~5s of overhead per run
- We don't use any of Lean's complex features (options, real-time fills, brokerage integration)

Migration is documented in commits `faf4813` (initial migration) and onward.

### 5.2 Pluggable backend, Python default
`sim_engine.py` supports two backends for `simulate_buy_and_hold` and `simulate_rebalance_portfolio`:

| Backend | Per-run time | Notes |
|---|---|---|
| **`python` (default)** | **~0.4s end-to-end** | Pure Python loops. No NumPy/Numba startup cost (lazy imports). |
| `vectorbt` | ~4.4s end-to-end | vbt.Portfolio.from_orders + TargetPercent. Verified to match python backend to **$0.00** precision. Slower because Numba JIT + DataFrame setup overhead dwarfs actual computation at our 30K-data-point scale. |

Pick `vectorbt` only when you want to do **parameter sweeps** — its strength is running 100s of variants in one batched call. For our current 11-variant manual comparison, python wins.

```python
# Programmatic:
from sim_engine import set_backend
set_backend('vectorbt')

# Or env var:
SIM_BACKEND=vectorbt python3.11 strategies/tqqq_diversified/run_backtest.py

# Or CLI:
python3.11 strategies/tqqq_diversified/run_backtest.py --backend vectorbt
```

### 5.3 Timing strategies stay pure Python (both backends)
Timing1/Timing2 do **partial per-asset rebalancing** — only TQQQ↔QQQ are touched on monthly checks, fixed legs (BTAL/GLD/XLP/CURE) deliberately drift. vectorbt's `TargetPercent` mode would force re-rebalancing ALL legs on swap days. Expressing the partial-swap cleanly in vectorbt requires `from_order_func_nb` (Numba callbacks). Not worth the complexity at our data scale. **Pure Python only**.

### 5.4 Stats / Beta / Diversification Ratio stay pure Python
Our metric definitions don't match `vbt.Portfolio.stats()` 1:1:
- Ulcer Index: our def is `sqrt(mean(dd_pct²))` over the whole period including 0% periods
- UPI: `CAGR / Ulcer Index`
- Longest DD: in **years** (`days / 252`), with start/end dates returned
- Beta: skips zero-zero return pairs (weekend artifacts)

Code in `sim_engine.py` is the source of truth — the JS recompute inside `generate_report.py` mirrors these definitions.

### 5.5 Data format = Lean daily zips
We kept the Lean data format because:
- It's compact (~80KB per ETF for 14 years)
- The existing `download_data.py` converts yfinance auto-adjusted prices into this format (price × 10000 stored as int)
- Future migration to Lean is trivial if we ever need it

CSV inside zip: `yyyyMMdd HH:mm,Open*10000,High*10000,Low*10000,Close*10000,Volume`

### 5.6 yfinance `auto_adjust=True`
Prices in `Data/equity/usa/daily/*.zip` are dividend-adjusted (yfinance's auto-adjust mode). So `simulate_buy_and_hold` returns the "total return" including reinvested dividends. Stooq comparison showed our SPY values are ~$2 lower than raw close prices on recent dates, consistent with cumulative dividend adjustment.

### 5.7 HTML report architecture
- Server-side (Python): runs the 11 simulations, embeds equity curves as JS arrays, computes initial stats table
- Client-side (JS): time range selector triggers `recalcAll()` which:
  1. Re-bases each equity curve to $100K at the selected start
  2. Recomputes drawdown from rebased curve
  3. Recomputes all metrics in JS (Sharpe/Sortino/MaxDD/etc.)
  4. Calls `Plotly.react()` to update charts
  5. Regenerates Max DD / Longest DD annotations

The JS metric functions are line-by-line ports of the Python ones — **same definitions, same formulas**. We verified this earlier by running Python at `2020-01-01` start and comparing against JS-recomputed values; they matched.

### 5.8 Hover system
Plotly's native unified hover doesn't sort by value. We disabled Plotly's hover entirely (`hovermode: false`) and implemented our own:
- `mousemove` event on the chart div
- `xaxis.p2d()` converts pixel x → date
- Binary search finds closest data index across all traces
- Custom overlay div renders sorted list
- Custom spike line (absolute-positioned div)

This avoids Plotly's black-arrow hover markers and supports our sort-by-value requirement that Plotly doesn't natively offer.

---

## 6. Important quirks / things that broke before

1. **SPY benchmark went flat after 2021** — the original SPY data zip only had data through 2021-03-31, AND Lean's `factor_files/spy.csv` did a cumulative adjustment. We had to: (a) extend `hour/spy.zip` with synthesized 15:00 bars from daily, (b) set the factor file to passthrough (`1,1,0`) since our daily data is already dividend-adjusted. This only matters if you ever want to re-run the **Lean version** of the backtest — not relevant for the Python version in this repo.

2. **Beta was 0.35 for the main strategy (should be ~1.08)** — caused by:
   - Lean's equity curve sampling at 05:00 UTC (= previous day's close), creating a 1-day offset vs SPY's daily values
   - 31% of `eq_dates` were weekends/holidays causing zero-zero return pairs that diluted covariance
   
   Fix in this repo: compute beta from our pure-Python simulated equity curves (no Lean), skip non-trading days. **No longer an issue** since we never use Lean equity here.

3. **HTML browser hung when opened** — was caused by:
   - `Math.min(...arrayOf5000)` stack overflow in inline JS (huge spread args)
   - `Plotly.relayout` inside `plotly_relayout` event handler → infinite recursion
   - Both fixed; the report now loads in <1s.

4. **vectorbt rebalance differed from pure Python** — was off by ~10% in Timing1. Root cause: `TargetPercent` rebalances ALL assets when ANY target is set, but our strategy only wants to swap TQQQ↔QQQ. Fixed by keeping Timing in pure Python.

---

## 7. Common tasks

### Add a new strategy variant (e.g. Timing3)
1. Add `simulate_timing3(...)` function to `sim_engine.py` (copy Timing1 as template, modify swap logic)
2. In `generate_report.py`:
   - Add a `comp_t3_eq, t3_log = simulate_timing3(...)` call alongside the other timing sims
   - Add corresponding stats/beta/ann_returns computations
   - Add an entry to the `_portfolios` registry (it auto-generates table rows and dispatches to chart/log builders)
   - Add the trace to chart traces in the JS section (search for `comp_t1_eq` to find all spots)

### Backtest a different date range
```bash
python3.11 strategies/tqqq_diversified/run_backtest.py --start 2017-01-01 --end 2026-04-06
```
Or use the time range selector in the HTML report (no re-run needed).

### Add new ticker data
```bash
python3.11 strategies/tqqq_diversified/download_data.py NEW_TICKER ANOTHER_ONE
# Will be saved to Data/equity/usa/daily/new_ticker.zip
```

### Sanity check vectorbt vs python backend
```bash
python3.11 strategies/tqqq_diversified/run_backtest.py --backend python --no-open
mv output/latest/TQQQPortfolioStrategy.json /tmp/py.json
python3.11 strategies/tqqq_diversified/run_backtest.py --backend vectorbt --no-open
diff <(jq -S . /tmp/py.json) <(jq -S . output/latest/TQQQPortfolioStrategy.json)
```
Should be empty (both backends produce byte-identical JSON).

---

## 8. Open / future items

**Things we explicitly skipped**:
- Transaction cost modeling (no fees/slippage in `simulate_*` — fine for the long-term annual rebalance + monthly timing strategies where cost is negligible, would need attention for higher-freq strategies)
- Parameter sweep utility (vectorbt backend is ready for it, but no `param_sweep.py` exists yet)
- Tests beyond ad-hoc regression scripts

**Easy wins available**:
- Cross-strategy comparison page (currently each backtest is one strategy + variants; could add a meta-page comparing across strategy folders)
- Walk-forward / out-of-sample analysis
- Bootstrap confidence intervals on CAGR

---

## 9. Where to look first when something breaks

1. **Numbers off**: run `sim_engine.py` directly with both backends and `diff` the equity curves. If they disagree, something regressed in the python backend (it's the source of truth).

2. **HTML doesn't load / hangs**: open Chrome DevTools, check for JS errors. The most common causes are: oversized inline arrays (we already use rounded floats and deduplicated dates to keep size <2MB), or `Plotly.react` triggering events that re-enter `recalcAll` (look for guard flags like `_ddUpdating`).

3. **Data missing for recent dates**: `python3.11 strategies/tqqq_diversified/download_data.py` does incremental updates; check yfinance is reachable. Note: the script falls back to `yf.Ticker(...).history()` if `yf.download()` hits SSL errors (curl_cffi flaky on some networks).

4. **vectorbt backend errors**: it lazy-imports vectorbt only when used, so a missing install only surfaces with `--backend vectorbt`. Reinstall: `pip install vectorbt==0.28.5`.

---

## 10. Quick-start verification

After cloning fresh / pulling new changes:

```bash
cd ~/work/vectorbt
git status                      # should be on us-market-strategy-bt
python3.11 strategies/tqqq_diversified/run_backtest.py --no-open
# Should complete in ~0.4s, produce output/latest/TQQQPortfolioStrategy.html
# Expected: End equity ~$1,985,549 for default 2011-09-14 → 2026-04-06 range
```

If end equity differs, the simulation logic regressed. The exact figure should be `$1,985,549.45` (matches across all prior runs).

---

## 11. Related external repos

- **`~/work/Lean`**: the original Lean-based version. Strategy code is at `Algorithm.Python/strategies/`. Kept for reference; do not actively develop there.

- **Upstream `polakowo/vectorbt`**: pinned via the `upstream` remote. Pull from it if you want to update the vectorbt library itself:
  ```bash
  git fetch upstream
  git merge upstream/master   # or upstream/main, check which exists
  ```
