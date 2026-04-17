#!/usr/bin/env python3.11
"""
Download historical daily OHLCV data via yfinance and convert to Lean format.

Features:
  - Command-line ticker specification
  - Skip if data is already up-to-date (last row >= last completed business day)
  - Incremental: only fetch the missing tail, merge into the existing zip
  - Full re-download mode (--full) to overwrite everything
  - Fallback from yf.download() to Ticker.history() on SSL / curl errors

Lean daily equity format:
  Data/equity/usa/daily/<ticker_lower>.zip
    └── <ticker_lower>.csv   (no header)
        yyyyMMdd HH:mm, Open*10000, High*10000, Low*10000, Close*10000, Volume

Usage examples:
  # Incremental update of all default tickers
  python3.11 download_data.py

  # Specify tickers explicitly
  python3.11 download_data.py MSFT AAPL QQQ

  # Via --tickers flag (can mix with positional)
  python3.11 download_data.py --tickers TQQQ BTAL GLD XLP CURE SPY

  # Force full re-download (ignore existing data)
  python3.11 download_data.py --full

  # Custom data directory
  python3.11 download_data.py --data-dir /path/to/Data/equity/usa/daily
"""

import argparse
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TICKERS: list[str] = ["TQQQ", "BTAL", "GLD", "XLP", "CURE", "SPY"]

REPO_ROOT        = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "Data" / "equity" / "usa" / "daily"

RETRY_ATTEMPTS = 5
RETRY_DELAY    = 3   # seconds between retries


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def last_completed_trading_day() -> date:
    """Return the most recent weekday before today (ignores market holidays).

    We treat yesterday (if a weekday) as the last 'closed' session, which
    is conservative — today's session may or may not be closed yet.
    """
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:   # Saturday=5, Sunday=6
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Lean zip I/O
# ---------------------------------------------------------------------------

def read_lean_zip(zip_path: Path) -> pd.DataFrame:
    """Read an existing Lean daily zip into a DataFrame (prices in $, not deci-cents).

    Returns an empty DataFrame if the file does not exist.
    Index: DatetimeIndex (timezone-naive), Columns: Open High Low Close Volume.
    """
    if not zip_path.exists():
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    with zipfile.ZipFile(zip_path, "r") as zf:
        content = zf.read(zf.namelist()[0]).decode()

    rows = []
    for line in content.strip().splitlines():
        parts = line.split(",")
        if len(parts) < 6:
            continue
        dt     = datetime.strptime(parts[0], "%Y%m%d %H:%M")
        open_  = float(parts[1]) / 10_000
        high   = float(parts[2]) / 10_000
        low    = float(parts[3]) / 10_000
        close  = float(parts[4]) / 10_000
        volume = float(parts[5])
        rows.append((dt, open_, high, low, close, volume))

    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    df = pd.DataFrame(rows, columns=["dt", "Open", "High", "Low", "Close", "Volume"])
    df.set_index("dt", inplace=True)
    df.sort_index(inplace=True)
    return df


def to_lean_csv(df: pd.DataFrame) -> str:
    """Serialize a DataFrame to Lean CSV (no header, prices * 10000 as ints)."""
    rows = []
    for dt, row in df.iterrows():
        rows.append(
            f"{dt.strftime('%Y%m%d 00:00')},"
            f"{int(round(float(row['Open'])  * 10_000))},"
            f"{int(round(float(row['High'])  * 10_000))},"
            f"{int(round(float(row['Low'])   * 10_000))},"
            f"{int(round(float(row['Close']) * 10_000))},"
            f"{int(row['Volume'])}"
        )
    return "\n".join(rows)


def save_lean_zip(zip_path: Path, ticker: str, df: pd.DataFrame) -> None:
    """Write DataFrame as a Lean-format zip (overwrites if exists)."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ticker.lower()}.csv", to_lean_csv(df))


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _fetch(ticker: str,
           start: date | None = None,
           end:   date | None = None) -> pd.DataFrame:
    """Try yf.download(), then Ticker.history() as fallback.

    start/end are inclusive dates.  Pass None to use the full history.
    Returns a clean OHLCV DataFrame with timezone-naive DatetimeIndex.
    Raises RuntimeError after all retries are exhausted.
    """
    start_str = start.strftime("%Y-%m-%d") if start else None
    end_str   = (end + timedelta(days=1)).strftime("%Y-%m-%d") if end else None
    # yfinance end= is exclusive, so +1 day

    last_exc = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):

        # ── Primary: yf.download ──────────────────────────────────────────
        try:
            kwargs: dict = dict(
                auto_adjust=True, progress=False, multi_level_index=False
            )
            if start_str:
                kwargs["start"] = start_str
            else:
                kwargs["period"] = "max"
            if end_str:
                kwargs["end"] = end_str

            df = yf.download(ticker, **kwargs)
            if df is not None and len(df) > 0:
                df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
                df.index = pd.to_datetime(df.index).tz_localize(None)
                df.sort_index(inplace=True)
                return df
        except Exception as exc:
            last_exc = exc

        # ── Fallback: Ticker.history ──────────────────────────────────────
        try:
            t = yf.Ticker(ticker)
            kwargs2: dict = dict(auto_adjust=True)
            if start_str:
                kwargs2["start"] = start_str
            else:
                kwargs2["period"] = "max"
            if end_str:
                kwargs2["end"] = end_str

            df = t.history(**kwargs2)
            if df is not None and len(df) > 0:
                df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
                df.index = pd.to_datetime(df.index).tz_localize(None)
                df.sort_index(inplace=True)
                return df
        except Exception as exc:
            last_exc = exc
            print(f"    [{ticker}] attempt {attempt}/{RETRY_ATTEMPTS} failed: {exc}")

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"All {RETRY_ATTEMPTS} attempts failed for {ticker}: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Per-ticker logic
# ---------------------------------------------------------------------------

def process_ticker(ticker: str, data_dir: Path, full: bool) -> dict:
    """Download / update one ticker.  Returns a status dict."""
    zip_path = data_dir / f"{ticker.lower()}.zip"
    last_trading_day = last_completed_trading_day()

    # ── 1. Read existing data ─────────────────────────────────────────────
    existing = pd.DataFrame()
    if not full and zip_path.exists():
        existing = read_lean_zip(zip_path)

    # ── 2. Up-to-date check ───────────────────────────────────────────────
    if not existing.empty:
        last_date = existing.index[-1].date()
        if last_date >= last_trading_day:
            return {
                "status":  "skipped",
                "reason":  f"already up-to-date (last row: {last_date})",
                "rows":    len(existing),
                "start":   existing.index[0].date(),
                "end":     last_date,
            }

    # ── 3. Determine fetch window ─────────────────────────────────────────
    if existing.empty or full:
        fetch_start = None                                 # full history
        fetch_end   = date.today()
        mode        = "full"
    else:
        fetch_start = existing.index[-1].date() + timedelta(days=1)
        fetch_end   = date.today()
        mode        = f"incremental from {fetch_start}"

    # ── 4. Download ───────────────────────────────────────────────────────
    new_data = _fetch(ticker, start=fetch_start, end=fetch_end)

    # ── 5. Merge ──────────────────────────────────────────────────────────
    if not existing.empty and not full:
        combined = pd.concat([existing, new_data])
        combined = combined[~combined.index.duplicated(keep="last")]  # new wins
        combined.sort_index(inplace=True)
    else:
        combined = new_data

    # ── 6. Save ───────────────────────────────────────────────────────────
    save_lean_zip(zip_path, ticker, combined)

    return {
        "status":    "ok",
        "mode":      mode,
        "new_rows":  len(new_data),
        "total":     len(combined),
        "start":     combined.index[0].date(),
        "end":       combined.index[-1].date(),
        "file":      zip_path,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download / incrementally update Lean daily equity data via yfinance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "tickers_positional",
        metavar="TICKER",
        nargs="*",
        help="Tickers to process (positional). Merged with --tickers.",
    )
    p.add_argument(
        "--tickers", "-t",
        metavar="TICKER",
        nargs="+",
        default=[],
        help="Tickers to process (flag form). Merged with positional args.",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Force full re-download (ignore existing data).",
    )
    p.add_argument(
        "--data-dir",
        metavar="DIR",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Lean daily data directory. Default: {DEFAULT_DATA_DIR}",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # Merge positional + flag tickers; fall back to defaults if none given
    tickers: list[str] = list(dict.fromkeys(
        [t.upper() for t in args.tickers_positional + args.tickers]
        or DEFAULT_TICKERS
    ))

    data_dir: Path = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    print(f"Data directory : {data_dir}")
    print(f"Tickers        : {' '.join(tickers)}")
    print(f"Mode           : {'full re-download' if args.full else 'incremental'}")
    print(f"Last trade day : {last_completed_trading_day()}")
    print()

    results: dict[str, dict] = {}
    for ticker in tickers:
        print(f"  {ticker} ...", end=" ", flush=True)
        try:
            info = process_ticker(ticker, data_dir, full=args.full)
            results[ticker] = info
            if info["status"] == "skipped":
                print(f"SKIP  {info['reason']}")
            else:
                print(
                    f"OK ({info['mode']})  "
                    f"+{info['new_rows']} new rows  "
                    f"total {info['total']}  "
                    f"{info['start']} → {info['end']}"
                )
        except Exception as exc:
            results[ticker] = {"status": "error", "error": str(exc)}
            print(f"ERROR: {exc}")
        time.sleep(1)   # polite rate-limiting

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n=== Summary ===")
    col = max(len(t) for t in tickers)
    ok      = [t for t, r in results.items() if r["status"] == "ok"]
    skipped = [t for t, r in results.items() if r["status"] == "skipped"]
    failed  = [t for t, r in results.items() if r["status"] == "error"]

    for ticker, info in results.items():
        s = info["status"].upper()
        if info["status"] == "ok":
            print(f"  {ticker:{col}}  {s:7}  {info['total']} rows  "
                  f"{info['start']} → {info['end']}  (+{info['new_rows']} new)")
        elif info["status"] == "skipped":
            print(f"  {ticker:{col}}  {s:7}  {info['rows']} rows  "
                  f"{info['start']} → {info['end']}")
        else:
            print(f"  {ticker:{col}}  {s:7}  {info['error']}")

    print(f"\nUpdated: {len(ok)}  Skipped: {len(skipped)}  Failed: {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
