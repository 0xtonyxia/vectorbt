# TQQQ Diversified — Portfolio Backtest

A pure-Python backtest framework for a **leveraged + hedged ETF strategy** and a
family of **volatility-timing variants**. It started on QuantConnect's Lean, then
was migrated to pure Python in this repo (Lean was overkill — see
[Why pure Python](#why-pure-python)).

This strategy lives inside a fork of
[polakowo/vectorbt](https://github.com/polakowo/vectorbt)
(`0xtonyxia/vectorbt`, branch `us-market-strategy-bt`): we use vectorbt as an
optional library backend while hosting the strategy code under `strategies/`.
Upstream is preserved as the `upstream` remote.

The deliverable is a **single interactive HTML report** comparing 19 portfolios
side-by-side (stats table + Performance / Drawdown / Returns / Allocation /
Rebalance Log tabs, client-side time-range recompute, sorted hover, zoom-aware
Max-DD annotations).

---

## Quick start

```bash
cd ~/work/vectorbt

# One command: refresh data → backtest 2019-05-08 → latest → open HTML
python3.11 strategies/tqqq_diversified/run_backtest.py
```

`run_backtest.py` does an incremental data refresh first (best-effort — a
network/rate-limit failure just falls back to existing data), runs all
simulations, writes the report, and opens it in the browser.

Useful flags:

| Flag | Effect |
|---|---|
| `--no-update` | Skip the pre-backtest data refresh (use existing data) |
| `--no-open` | Don't auto-open the browser |
| `--no-report` | Skip HTML generation (base strategy only) |
| `--start YYYY-MM-DD` | Custom start (default `2019-05-08`, DBMF inception) |
| `--end YYYY-MM-DD` | Custom end (default: latest available trading day) |
| `--backend python\|vectorbt` | Simulation backend (default `python`) |

> **Proxy note (data refresh):** Yahoo aggressively rate-limits this user's home
> ISP. `run_backtest.py` auto-routes the refresh through a local proxy at
> `127.0.0.1:12334` **only if** no proxy env var is already set **and** that port
> is listening — so it's a no-op on other networks / when the proxy is down. To
> force a proxy manually:
> `export https_proxy=http://127.0.0.1:12334 http_proxy=http://127.0.0.1:12334 all_proxy=socks5://127.0.0.1:12334`

You can also run the data refresh on its own:

```bash
python3.11 strategies/tqqq_diversified/download_data.py            # all default tickers, incremental
python3.11 strategies/tqqq_diversified/download_data.py NEW_TICKER # add a ticker → new_ticker.zip
python3.11 strategies/tqqq_diversified/download_data.py --full     # force full re-download
```

---

## The strategies

### Bases (annual rebalance, first trading day of each calendar year)

| Base | Weights |
|---|---|
| **BTAL base** | 35% TQQQ + 30% BTAL + 15% GLD + 15% XLP + 5% CURE |
| **DBMF base** | 25% TQQQ + 40% DBMF + 15% GLD + 15% XLP + 5% CURE |
| **DBBT base** | 30% TQQQ + 20% DBMF + 15% BTAL + 15% GLD + 15% XLP + 5% CURE |

Legs: **TQQQ** 3x QQQ (growth engine) · **BTAL** anti-beta long/short hedge ·
**DBMF** managed-futures / trend hedge · **GLD** gold · **XLP** consumer staples ·
**CURE** 3x healthcare. The DBMF base tilts toward the trend hedge and away from
leverage; the DBBT base splits the hedge bucket between trend-following and
anti-beta.

### Timing variants

On the **first trading day of each month** (or quarter for RV60), check QQQ's
realized volatility and adjust the **attack leg** (TQQQ↔QQQ). All other legs drift
freely between annual rebalances — only the attack leg is touched on timing checks.

- **Timing1** (all-or-nothing): `RV > threshold` → swap attack leg fully to QQQ;
  `RV ≤ threshold` → swap fully back to TQQQ.
- **Timing2** (partial): `RV20 > threshold` → attack leg = 25% TQQQ + 75% QQQ;
  else 100% TQQQ.

**The annual rebalance is volatility-aware too.** It does *not* blindly snap the
attack leg back to TQQQ: it rebalances all legs to base weights, then sets the
attack leg according to the current RV20 regime at the rebalance date (Timing1:
hold QQQ if `RV > threshold`; Timing2: apply the 25/75 split if `RV > threshold`).
This mirrors the monthly-check logic so a high-vol January doesn't force a 3x
re-entry. (`sim_engine.py` `simulate_timing1` / `simulate_timing2`.)

| Family | Variants |
|---|---|
| Timing1 on BTAL base | `Timing1-BTAL-RV20-22%`, `-25%`, `-20%`, `Timing1-BTAL-RV60-22%` (60-day RV, quarterly) |
| Timing2 on BTAL base | `Timing2-BTAL-RV20-22%`, `-25%`, `-20%` |
| Timing1 on DBMF base | `Timing1-DBMF-RV20-22%`, `-25%`, `-20%` |
| Timing1 on DBBT base | `Timing1-DBBT-RV20-22%`, `-25%`, `-20%` |

### Benchmarks

`100% SPY` (buy-and-hold) · `100% QQQ` (buy-and-hold) · `50% BTAL + 50% TQQQ`
(annual rebalance).

**Default window** is `2019-05-08 → latest` (DBMF inception, so all three base
families share the same window). Use `--start 2011-09-14` for the long history
(DBMF/DBBT series are flat pre-2019).

---

## Repo structure

```
~/work/vectorbt/
├── vectorbt/ tests/ docs/ ...      # upstream vectorbt library (untouched)
│
├── Data/equity/usa/daily/          # Daily OHLCV zips (Lean format)
│   ├── tqqq.zip btal.zip gld.zip xlp.zip cure.zip dbmf.zip
│   └── spy.zip qqq.zip bil.zip
│
├── strategies/tqqq_diversified/
│   ├── README.md                   # this file
│   ├── sim_engine.py               # simulation + metrics engine (pluggable backend)
│   ├── run_backtest.py             # driver: refresh data → run base → invoke report
│   ├── generate_report.py          # builds the interactive HTML (runs all 19 sims)
│   └── download_data.py            # yfinance → Lean-format daily zips
│
└── output/<strategy>-<ts>/         # per-run output (gitignored)
    ├── TQQQPortfolioStrategy.json  ·  .html  ·  -log.txt
```

**Naming convention:** each strategy lives in `strategies/<name>/`. To add a new
strategy, copy `tqqq_diversified/` and adapt. The 4 files are self-contained — no
shared modules across strategies yet (refactor only when a second strategy exists
and duplication actually hurts).

---

## Architecture & key decisions

### Why pure Python
Lean was removed because ~99% of the computation already lived in our Python
`simulate_*` functions (comparison portfolios were never Lean backtests), Lean's
equity curve had a 1-day UTC artifact that broke Beta, dotnet added ~5s startup,
and we use none of Lean's advanced features. Migration: commit `faf4813` onward.

### Pluggable backend, Python default
`sim_engine.py` supports two backends for `simulate_buy_and_hold` /
`simulate_rebalance_portfolio`:

| Backend | Per-run | Notes |
|---|---|---|
| **`python` (default)** | **~0.4s** | Pure Python loops; numpy/pandas/vectorbt lazy-imported (never touched on this path). |
| `vectorbt` | ~4.4s | `vbt.Portfolio.from_orders` + TargetPercent. Verified to match python to **$0.00**. Slower at our ~30K-point scale (Numba JIT + DataFrame overhead dwarfs the work). |

Pick `vectorbt` only for **parameter sweeps**. Select via `--backend`,
`SIM_BACKEND=vectorbt`, or `set_backend('vectorbt')`.

### Timing stays pure Python (both backends)
Timing1/2 do **partial per-asset rebalancing** (only TQQQ↔QQQ on checks; fixed legs
drift). vectorbt's `TargetPercent` re-rebalances *all* legs when any target is set,
which once made Timing1 differ by ~10%. Expressing the partial swap cleanly needs
`from_order_func_nb` (Numba callbacks) — not worth it. **Timing is pure Python only.**

### Metrics are pure Python (source of truth)
Our definitions don't match `vbt.Portfolio.stats()` 1:1:
- Ulcer Index = `sqrt(mean(dd_pct²))` over the whole period (including 0% spans)
- UPI = `CAGR / Ulcer Index`
- Longest DD in **years** (`days/252`), with start/end dates
- Beta skips zero-zero return pairs (weekend artifacts)

`sim_engine.py` is the source of truth; the JS recompute in `generate_report.py`
is a line-by-line port of the same formulas (verified to match).

### Data format = Lean daily zips
`Data/equity/usa/daily/<ticker>.zip` → `<ticker>.csv`, no header:
`yyyyMMdd HH:mm,Open*10000,High*10000,Low*10000,Close*10000,Volume`. Compact
(~80KB/ETF for 14y) and trivially re-importable into Lean if ever needed.

Prices are **dividend-adjusted** (yfinance `auto_adjust=True`), so buy-and-hold
returns are total returns. `download_data.py` updates incrementally (appends only
new rows; old rows keep their original adjustment) and drops incomplete trailing
rows with NaN OHLC (a provisional session for a less-liquid ETF, e.g. DBMF, would
otherwise crash the int conversion).

### HTML report architecture
- **Server-side (Python):** runs all 19 simulations, embeds equity curves as
  rounded JS arrays, computes the initial stats table.
- **Client-side (JS):** the time-range selector triggers `recalcAll()` →
  re-base each curve to $100K at the selected start → recompute drawdown +
  all metrics in JS → `Plotly.react()` → regenerate Max/Longest-DD annotations.
- **Sorted hover:** Plotly's native hover can't sort by value, so it's disabled
  (`hovermode:false`) and reimplemented via a `mousemove` handler + `xaxis.p2d()`
  (plot-area-relative pixels) + binary search + a custom overlay/spike-line.

---

## Common tasks

**Add a variant (e.g. Timing3):**
1. Add `simulate_timing3(...)` to `sim_engine.py` (copy Timing1, modify swap logic).
2. In `generate_report.py`: add the `simulate_timing3(...)` call, its
   stats/beta/annual-returns, an entry in the `_portfolios` registry (auto-generates
   table rows + dispatches charts/logs), and the trace in the JS section
   (grep `comp_t1_eq` to find every spot).

**Different date range:** `--start`/`--end`, or just use the in-report time-range
selector (no re-run needed).

**Sanity-check the two backends produce identical JSON:**
```bash
python3.11 strategies/tqqq_diversified/run_backtest.py --backend python  --no-open --no-update
mv output/latest/TQQQPortfolioStrategy.json /tmp/py.json
python3.11 strategies/tqqq_diversified/run_backtest.py --backend vectorbt --no-open --no-update
diff <(jq -S . /tmp/py.json) <(jq -S . output/latest/TQQQPortfolioStrategy.json)   # expect empty
```

---

## Troubleshooting

1. **Numbers look off** — run `sim_engine.py` with both backends and `diff` the
   equity curves. If they disagree, the python backend (source of truth) regressed.
2. **HTML hangs / won't load** — open DevTools, check JS errors. Past causes:
   oversized inline arrays (we round floats + dedupe dates to stay <2MB) and
   `Plotly.react` re-entering `recalcAll` (look for guards like `_ddUpdating`).
3. **Data missing for recent dates** — the refresh is incremental; check yfinance
   is reachable and the proxy is up (see the proxy note above). Without it you'll
   see `YFRateLimitError` with no working fallback.
4. **`--backend vectorbt` errors** — vectorbt is lazy-imported, so a missing
   install only surfaces here. `pip install vectorbt==0.28.5`.

## Verification anchor

Stable regression check (fixed window, unaffected by data refreshes since old
rows keep their adjustment):

```bash
python3.11 strategies/tqqq_diversified/run_backtest.py \
  --start 2011-09-14 --end 2026-04-06 --no-update --no-open --no-report
# Base strategy end equity should be exactly $1,985,549.45
```

If that differs, the base simulation logic regressed. (DBMF/DBBT strategies are
flat pre-2019 over this window.)

## Dependencies

- Python 3.11
- `pandas`, `yfinance` (data download) — `yfinance` pulls in `curl_cffi`
- `numpy` + `vectorbt==0.28.5` — **optional**, only for `--backend vectorbt`
- The default python backend + report need no compiled deps

## Open / future items

Skipped on purpose: transaction-cost modeling (negligible for annual + monthly
strategies), a parameter-sweep utility (vectorbt backend is ready for it), formal
tests beyond ad-hoc regression checks. Easy wins: cross-strategy comparison page,
walk-forward / out-of-sample analysis, bootstrap CIs on CAGR.

## Related repos

- **`~/work/Lean`** — original Lean version (`Algorithm.Python/strategies/`). Kept
  for reference; do not actively develop there.
- **Upstream `polakowo/vectorbt`** — pinned via the `upstream` remote. To update
  the library itself: `git fetch upstream && git merge upstream/master`.
