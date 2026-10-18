"""
price_cache.py  —  MFM v4
EOD price cache for the full NSE + BSE universe.

Price sources (in priority order):
  1. NSE bhavcopy  — official VWAP-based closing price for all NSE EQ-series stocks.
                     Published by NSE ~18:30–19:00 IST. One HTTP request covers the
                     entire NSE universe (~2,200+ stocks). Exact match to what you see
                     on NSE, Zerodha, MoneyControl.
  2. yfinance      — fallback for BSE-only stocks, index benchmarks (^NSEI, ^BSESN),
                     and any NSE ticker absent from the bhavcopy (suspended, SME, etc.).

Schema (price_cache.parquet):
    ticker      str   yfinance ticker e.g. "RELIANCE.NS" or "500325.BO"
    last_close  float last confirmed EOD closing price (₹)
    prev_close  float closing price of the prior trading day
    close_date  date  trading date of last_close

Derived metrics (day_change_pct, log returns, beta, etc.) are computed in
memory — never stored. Schema stays stable as analytics requirements grow.

Artifacts written on each build:
    price_cache.parquet        — the data (atomic write via .tmp rename)
    price_build_status.json    — live progress for UI polling
    price_cache_metadata.json  — audit trail: timing, row counts, failure count
    missing_tickers.log        — per-ticker failure log with error reason
"""

import io, os, json, math, time, threading, zipfile, concurrent.futures
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import yfinance as yf

PRICE_CACHE_FILE    = "price_cache.parquet"
PRICE_STATUS_FILE   = "price_build_status.json"
PRICE_METADATA_FILE = "price_cache_metadata.json"
MISSING_TICKERS_LOG = "missing_tickers.log"

BATCH_SIZE          = 50
BATCH_SLEEP         = 0.3        # seconds between yfinance batches (rate-limit buffer)
BATCH_TIMEOUT       = 60         # seconds before a hung yfinance call is aborted
BENCHMARK_TICKERS   = ["^NSEI", "^BSESN"]

IST = ZoneInfo("Asia/Kolkata")
# NSE bhavcopy is published ~18:30–19:00 IST; 19:30 gives a comfortable buffer.
_BHAVCOPY_AVAILABLE_BUFFER = dtime(19, 30)

_BUILD_COOLDOWN_HOURS = 4   # min hours between builds when data date hasn't advanced

_lock   = threading.Lock()
_thread: threading.Thread | None = None


# ─────────────────────────────────────────────────────────────────────
#  STATUS / METADATA HELPERS
# ─────────────────────────────────────────────────────────────────────

def _write_status(running: bool, total: int, done: int,
                  current: str, error: str = "") -> None:
    try:
        with open(PRICE_STATUS_FILE, "w") as f:
            json.dump({
                "running":    running,
                "total":      total,
                "done":       done,
                "current":    current,
                "error":      error,
                "updated_at": datetime.now().isoformat(),
            }, f)
    except Exception:
        pass


def get_price_build_status() -> dict:
    """Read build progress. Returns safe defaults when file is absent."""
    default = {"running": False, "total": 0, "done": 0, "current": "", "error": ""}
    if not os.path.exists(PRICE_STATUS_FILE):
        return default
    try:
        with open(PRICE_STATUS_FILE) as f:
            return {**default, **json.load(f)}
    except Exception:
        return default


def is_price_build_running() -> bool:
    return get_price_build_status().get("running", False)


def _write_metadata(start: float, end: float, rows: int, failures: int) -> None:
    try:
        with open(PRICE_METADATA_FILE, "w") as f:
            json.dump({
                "last_build_start":            datetime.fromtimestamp(start).isoformat(),
                "last_build_complete":          datetime.fromtimestamp(end).isoformat(),
                "last_build_duration_seconds":  round(end - start, 1),
                "rows_written":                rows,
                "failures":                    failures,
            }, f, indent=2)
    except Exception:
        pass


def _log_missing(ticker: str, reason: str) -> None:
    try:
        with open(MISSING_TICKERS_LOG, "a") as f:
            f.write(f"{ticker},{reason}\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
#  STALENESS
# ─────────────────────────────────────────────────────────────────────

def _last_weekday(d: date) -> date:
    """Return d if it is a weekday, otherwise step back to the previous Friday."""
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


def is_price_cache_stale() -> bool:
    """
    Returns True when the price cache is absent or out of date.

    Target date logic (19:30 IST buffer — bhavcopy is published ~18:30–19:00):
      - After 19:30 IST on a weekday → today's close should be in bhavcopy.
      - Otherwise → the last weekday's close is the target.

    Cooldown guard: on market holidays (weekday, but no bhavcopy published),
    the target date will never be satisfied.  To prevent an infinite rebuild loop,
    treat the cache as fresh if a completed build ran within _BUILD_COOLDOWN_HOURS.
    """
    if not os.path.exists(PRICE_CACHE_FILE):
        return True
    try:
        df = pd.read_parquet(PRICE_CACHE_FILE)
        if df.empty or "close_date" not in df.columns:
            return True
        latest_cached = pd.to_datetime(df["close_date"]).dt.date.max()

        now_ist = datetime.now(IST)
        if now_ist.weekday() < 5 and now_ist.time() >= _BHAVCOPY_AVAILABLE_BUFFER:
            target = now_ist.date()
        else:
            target = _last_weekday(now_ist.date())

        if latest_cached >= target:
            return False

        # Cooldown: if a build ran recently and produced the same close_date,
        # the market was likely closed (holiday) — don't loop.
        if os.path.exists(PRICE_METADATA_FILE):
            try:
                with open(PRICE_METADATA_FILE) as f:
                    meta = json.load(f)
                completed_str = meta.get("last_build_complete", "")
                if completed_str:
                    completed_dt = datetime.fromisoformat(completed_str)
                    hours_since = (datetime.now() - completed_dt).total_seconds() / 3600
                    if hours_since < _BUILD_COOLDOWN_HOURS:
                        return False
            except Exception:
                pass

        return True
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────────
#  NSE BHAVCOPY
# ─────────────────────────────────────────────────────────────────────

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection":      "keep-alive",
}


def _nse_session() -> requests.Session:
    """Return a lightweight requests.Session with NSE-compatible headers."""
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    return s


def _fetch_nse_bhavcopy(trade_date: date) -> pd.DataFrame | None:
    """
    Download and parse the NSE full security bhavcopy for *trade_date*.

    Uses the public endpoint:
        https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv

    This endpoint does not require cookies or Cloudflare authentication.

    Returns a DataFrame with columns:
        ticker      str    e.g. "RELIANCE.NS"
        last_close  float  official NSE closing price (VWAP of closing session)
        prev_close  float  prior day official close
        close_date  date   = trade_date

    Returns None when:
        - trade_date is a holiday (file doesn't exist on NSE server)
        - The bhavcopy is not yet published (too early in the day)
        - Network error / timeout
    """
    dd   = trade_date.strftime("%d")   # 02
    mm   = trade_date.strftime("%m")   # 04
    yyyy = trade_date.strftime("%Y")   # 2026

    url = (
        f"https://nsearchives.nseindia.com/products/content"
        f"/sec_bhavdata_full_{dd}{mm}{yyyy}.csv"
    )

    session = _nse_session()
    try:
        resp = session.get(url, timeout=45)
    except Exception:
        return None

    if not resp.ok:
        return None

    ct = resp.headers.get("content-type", "")
    if "html" in ct.lower():
        return None

    try:
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception:
        return None

    # Column names in the sec_bhavdata_full format:
    #   SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE,
    #   LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY,
    #   TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
    #
    # Normalise: strip whitespace from column names (NSE sometimes pads them).
    df.columns = df.columns.str.strip()

    # Detect which close/prevclose column names are present
    close_col   = next((c for c in ["CLOSE_PRICE", "CLOSE"] if c in df.columns), None)
    prev_col    = next((c for c in ["PREV_CLOSE", "PREVCLOSE"] if c in df.columns), None)
    series_col  = next((c for c in ["SERIES"] if c in df.columns), None)
    symbol_col  = next((c for c in ["SYMBOL"] if c in df.columns), None)

    if not all([close_col, prev_col, series_col, symbol_col]):
        return None  # unexpected schema

    # Keep only EQ series
    df = df[df[series_col].str.strip() == "EQ"].copy()
    if df.empty:
        return None

    df["ticker"]     = df[symbol_col].str.strip() + ".NS"
    df["last_close"] = pd.to_numeric(df[close_col], errors="coerce")
    df["prev_close"] = pd.to_numeric(df[prev_col],  errors="coerce")

    # Prefer the trading date from the DATE1 column over the filename date.
    # The bhavcopy file is sometimes published on a market holiday (e.g. Good Friday)
    # but contains the previous trading day's data — DATE1 carries the actual date.
    date_col = next((c for c in ["DATE1"] if c in df.columns), None)
    if date_col is not None:
        try:
            actual_date = (
                pd.to_datetime(df[date_col].str.strip(), format="%d-%b-%Y", errors="coerce")
                .dropna()
                .dt.date
                .mode()
                .iloc[0]
            )
            df["close_date"] = actual_date
        except Exception:
            df["close_date"] = trade_date
    else:
        df["close_date"] = trade_date

    return df[["ticker", "last_close", "prev_close", "close_date"]].reset_index(drop=True)


def _find_latest_bhavcopy() -> tuple[date | None, pd.DataFrame | None]:
    """
    Walk back from today (or yesterday if before 19:30 IST) to find the most
    recent trading day with an available NSE bhavcopy (up to 7 calendar days).

    Returns (trade_date, DataFrame) or (None, None) if nothing is found.
    """
    now_ist = datetime.now(IST)

    # Start from today if it's after the availability buffer, else yesterday.
    if now_ist.weekday() < 5 and now_ist.time() >= _BHAVCOPY_AVAILABLE_BUFFER:
        candidate = now_ist.date()
    else:
        candidate = now_ist.date() - timedelta(days=1)

    for _ in range(7):
        if candidate.weekday() >= 5:       # skip weekends
            candidate -= timedelta(days=1)
            continue
        df = _fetch_nse_bhavcopy(candidate)
        if df is not None:
            return candidate, df
        candidate -= timedelta(days=1)

    return None, None


# ─────────────────────────────────────────────────────────────────────
#  NSE LIVE API  (Tier 2 — covers ~750 most-liquid NSE stocks)
# ─────────────────────────────────────────────────────────────────────

_NSE_INDEX_ENDPOINT = (
    "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20TOTAL%20MARKET"
)


def _fetch_nse_api_prices() -> pd.DataFrame | None:
    """
    Fetch closing prices for the ~750 most-liquid NSE stocks via the
    NIFTY TOTAL MARKET index endpoint.

    Used as Tier-2 fallback when the bhavcopy is unavailable (weekends,
    before 19:30 IST, or NSE archive server outage).

    Fields used:
        lastPrice       — last traded price of the most recent session
        previousClose   — official close of the session before that
        timestamp       — top-level field e.g. "02-Apr-2026 21:00:30",
                          parsed to derive close_date

    Returns DataFrame[ticker, last_close, prev_close, close_date] or None.
    """
    session = _nse_session()
    # NSE API requires at least one prior request to set cookies
    try:
        session.get("https://www.nseindia.com", timeout=10)
    except Exception:
        pass

    try:
        resp = session.get(_NSE_INDEX_ENDPOINT, timeout=20)
    except Exception:
        return None

    if not resp.ok:
        return None

    try:
        payload = resp.json()
    except Exception:
        return None

    stocks = payload.get("data", [])
    if not stocks:
        return None

    # Parse close_date from the API-level timestamp ("02-Apr-2026 21:00:30")
    ts_str = payload.get("timestamp", "")
    try:
        close_date = datetime.strptime(ts_str.split()[0], "%d-%b-%Y").date()
    except Exception:
        close_date = _last_weekday(datetime.now(IST).date())

    rows: list[dict] = []
    for s in stocks:
        sym = s.get("symbol", "").strip()
        if not sym:
            continue
        last = s.get("lastPrice")
        prev = s.get("previousClose")
        if last is None:
            continue
        rows.append({
            "ticker":     sym + ".NS",
            "last_close": float(last),
            "prev_close": float(prev) if prev is not None else float("nan"),
            "close_date": close_date,
        })

    if not rows:
        return None

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────
#  YFINANCE BATCH HELPER
# ─────────────────────────────────────────────────────────────────────

def _yf_fetch_batch(batch: list[str]) -> tuple[list[dict], int]:
    """
    Download the last 5 trading days for *batch* via yfinance with a hard timeout.
    Returns (results_list, failure_count).
    """
    results: list[dict] = []
    failures = 0

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        if len(batch) == 1:
            fut = executor.submit(
                yf.download, batch[0],
                period="5d", interval="1d", auto_adjust=True, progress=False,
            )
            try:
                raw = fut.result(timeout=BATCH_TIMEOUT)
            except concurrent.futures.TimeoutError:
                _log_missing(batch[0], "timeout")
                return [], 1
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            if raw.empty:
                _log_missing(batch[0], "empty dataframe")
                return [], 1
            close = raw["Close"].squeeze().dropna()
            if len(close) >= 1:
                results.append({
                    "ticker":     batch[0],
                    "last_close": float(close.iloc[-1]),
                    "prev_close": float(close.iloc[-2]) if len(close) >= 2 else float("nan"),
                    "close_date": close.index[-1].date(),
                })
            else:
                _log_missing(batch[0], "no close rows after dropna")
                failures += 1
        else:
            fut = executor.submit(
                yf.download, batch,
                period="5d", interval="1d", auto_adjust=True, progress=False,
                group_by="ticker",
            )
            try:
                raw = fut.result(timeout=BATCH_TIMEOUT)
            except concurrent.futures.TimeoutError:
                for tk in batch:
                    _log_missing(tk, "batch timeout")
                return [], len(batch)
            if raw.empty:
                for tk in batch:
                    _log_missing(tk, "batch returned empty dataframe")
                return [], len(batch)
            elif isinstance(raw.columns, pd.MultiIndex):
                for tk in batch:
                    try:
                        close = raw[tk]["Close"].dropna()
                        if len(close) >= 1:
                            results.append({
                                "ticker":     tk,
                                "last_close": float(close.iloc[-1]),
                                "prev_close": float(close.iloc[-2]) if len(close) >= 2 else float("nan"),
                                "close_date": close.index[-1].date(),
                            })
                        else:
                            _log_missing(tk, "no close rows in batch")
                            failures += 1
                    except Exception as exc:
                        _log_missing(tk, str(exc)[:80])
                        failures += 1
            else:
                # yfinance collapsed to single-ticker frame
                close = raw["Close"].squeeze().dropna()
                if len(close) >= 1:
                    results.append({
                        "ticker":     batch[0],
                        "last_close": float(close.iloc[-1]),
                        "prev_close": float(close.iloc[-2]) if len(close) >= 2 else float("nan"),
                        "close_date": close.index[-1].date(),
                    })
                else:
                    _log_missing(batch[0], "no close in collapsed single frame")
                    failures += 1
    except Exception as exc:
        for tk in batch:
            _log_missing(tk, f"batch exception: {str(exc)[:60]}")
        failures += len(batch)
    finally:
        executor.shutdown(wait=False)

    return results, failures


# ─────────────────────────────────────────────────────────────────────
#  BUILD
# ─────────────────────────────────────────────────────────────────────

def build_price_cache(
    tickers: list[str],
    progress_callback=None,
) -> pd.DataFrame:
    """
    Build the EOD price cache using two phases:

    Phase 1 — NSE bhavcopy (fast, authoritative):
        One HTTP request fetches the official closing prices for every NSE EQ
        stock.  Prices match exactly what NSE/Zerodha/MoneyControl report.

    Phase 2 — yfinance (fallback):
        Covers BSE-only stocks, index benchmarks, and any NSE ticker absent
        from the bhavcopy (suspended, SME board, etc.).

    Writes atomically: price_cache.tmp → price_cache.parquet.
    Failures logged to missing_tickers.log.
    """
    try:
        with open(MISSING_TICKERS_LOG, "w") as f:
            f.write("ticker,error\n")
    except Exception:
        pass

    nse_tickers   = [t for t in tickers if t.endswith(".NS")]
    bse_tickers   = [t for t in tickers if t.endswith(".BO")]
    yf_mandatory  = BENCHMARK_TICKERS + bse_tickers   # always via yfinance

    results:  list[dict] = []
    failures: int        = 0

    # ── Phase 1a: NSE bhavcopy (official VWAP close, ~19:30 IST+) ───
    if progress_callback:
        progress_callback(0, len(tickers), "NSE bhavcopy…")

    bhav_date, bhav_df = _find_latest_bhavcopy()
    covered_nse: set[str] = set()
    # canonical_nse_date: the last known valid NSE trading date, used to cap
    # yfinance close_dates that exceed it (Yahoo ignores Indian market holidays).
    canonical_nse_date: date | None = None

    if bhav_df is not None:
        bhav_lookup = bhav_df.set_index("ticker")
        # Use the actual trading date from the bhavcopy data (DATE1 column),
        # not the filename date — they differ when published on a holiday.
        canonical_nse_date = bhav_df["close_date"].iloc[0]
        for tk in nse_tickers:
            if tk in bhav_lookup.index:
                row = bhav_lookup.loc[tk]
                lc = float(row["last_close"]) if not pd.isna(row["last_close"]) else float("nan")
                pc = float(row["prev_close"]) if not pd.isna(row["prev_close"]) else float("nan")
                if math.isnan(lc):
                    _log_missing(tk, "bhavcopy: NaN close")
                    failures += 1
                else:
                    results.append({
                        "ticker":     tk,
                        "last_close": lc,
                        "prev_close": pc,
                        "close_date": canonical_nse_date,  # actual trading date from DATE1
                    })
                    covered_nse.add(tk)
        if progress_callback:
            progress_callback(len(covered_nse), len(tickers), "bhavcopy done, fetching BSE…")

    # ── Phase 1b: NSE live API (Tier 2 — ~750 liquid stocks, no bhavcopy needed) ─
    if not covered_nse:
        if progress_callback:
            progress_callback(0, len(tickers), "NSE live API…")
        api_df = _fetch_nse_api_prices()
        if api_df is not None:
            api_lookup = api_df.set_index("ticker")
            # Derive canonical date from the API response timestamp
            if canonical_nse_date is None and not api_df.empty:
                canonical_nse_date = api_df["close_date"].max()
            for tk in nse_tickers:
                if tk in api_lookup.index:
                    row = api_lookup.loc[tk]
                    lc = float(row["last_close"]) if not pd.isna(row["last_close"]) else float("nan")
                    pc = float(row["prev_close"]) if not pd.isna(row["prev_close"]) else float("nan")
                    if not math.isnan(lc):
                        results.append({
                            "ticker":     tk,
                            "last_close": lc,
                            "prev_close": pc,
                            "close_date": row["close_date"],
                        })
                        covered_nse.add(tk)
            if progress_callback:
                progress_callback(len(covered_nse), len(tickers), "NSE API done, fetching remainder…")

    # Fallback: if NSE sources both failed, derive canonical date from last weekday
    if canonical_nse_date is None:
        canonical_nse_date = _last_weekday(datetime.now(IST).date() - timedelta(days=1))

    # ── Phase 2: yfinance for BSE, benchmarks, and NSE misses ───────
    nse_misses   = [t for t in nse_tickers if t not in covered_nse]
    yf_tickers   = yf_mandatory + nse_misses
    yf_total     = len(yf_tickers)
    yf_done      = 0

    for i in range(0, yf_total, BATCH_SIZE):
        batch = yf_tickers[i : i + BATCH_SIZE]
        if not batch:
            continue
        if progress_callback:
            progress_callback(len(covered_nse) + yf_done, len(tickers), batch[0])

        batch_results, batch_failures = _yf_fetch_batch(batch)
        # Cap yfinance close_date at canonical_nse_date.
        # Yahoo Finance doesn't know Indian market holidays (e.g. Good Friday),
        # so it returns the holiday date as the latest trading date. We override
        # it with the authoritative NSE date derived from Tier 1/2.
        for r in batch_results:
            if r["close_date"] > canonical_nse_date:
                r["close_date"] = canonical_nse_date
        results  += batch_results
        failures += batch_failures
        yf_done  += len(batch)
        time.sleep(BATCH_SLEEP)

    if progress_callback:
        progress_callback(len(tickers), len(tickers), "done")

    cache_df = (
        pd.DataFrame(results)
        if results
        else pd.DataFrame(columns=["ticker", "last_close", "prev_close", "close_date"])
    )

    # Atomic write
    tmp = PRICE_CACHE_FILE + ".tmp"
    cache_df.to_parquet(tmp, index=False)
    os.replace(tmp, PRICE_CACHE_FILE)

    return cache_df


# ─────────────────────────────────────────────────────────────────────
#  BACKGROUND BUILD
# ─────────────────────────────────────────────────────────────────────

def start_background_price_build(tickers: list[str]) -> bool:
    """
    Spawn a daemon thread to build the price cache in the background.
    Returns True if started, False if a build is already running.
    Idempotent: safe to call on every Streamlit script rerun.
    """
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False

        _write_status(running=True, total=len(tickers), done=0, current="Initialising…")
        t0 = time.time()

        def _worker() -> None:
            try:
                def _cb(done: int, total: int, current: str) -> None:
                    _write_status(running=True, total=total, done=done, current=current)

                result_df = build_price_cache(tickers, progress_callback=_cb)
                t1 = time.time()
                _write_status(
                    running=False, total=len(tickers),
                    done=len(result_df), current="✦ Complete",
                )
                _write_metadata(
                    start=t0, end=t1,
                    rows=len(result_df),
                    failures=len(tickers) + len(BENCHMARK_TICKERS) - len(result_df),
                )
            except Exception as exc:
                _write_status(
                    running=False, total=len(tickers),
                    done=0, current="", error=str(exc),
                )

        _thread = threading.Thread(target=_worker, daemon=True)
        _thread.start()
        return True


# ─────────────────────────────────────────────────────────────────────
#  READ
# ─────────────────────────────────────────────────────────────────────

def load_price_cache() -> pd.DataFrame:
    """
    Load price_cache.parquet. Returns an empty DataFrame with the correct
    schema if the file is absent or unreadable.
    """
    _empty = pd.DataFrame(columns=["ticker", "last_close", "prev_close", "close_date"])
    if not os.path.exists(PRICE_CACHE_FILE):
        return _empty
    try:
        return pd.read_parquet(PRICE_CACHE_FILE)
    except Exception:
        return _empty


def get_price(ticker: str, cache_df: pd.DataFrame | None = None) -> dict:
    """
    Look up EOD price data for a single ticker from the cache.

    Returns a dict:
        last_close      float   most recent EOD close (NaN if not found)
        prev_close      float   prior trading day's close (NaN if not found)
        close_date      date | None
        day_change_pct  float   (last/prev - 1)*100, or NaN if prev is NaN/0
    """
    if cache_df is None:
        cache_df = load_price_cache()

    row = cache_df[cache_df["ticker"] == ticker]
    if row.empty:
        return {
            "last_close":     float("nan"),
            "prev_close":     float("nan"),
            "close_date":     None,
            "day_change_pct": float("nan"),
        }

    last  = float(row["last_close"].iloc[0])
    prev  = float(row["prev_close"].iloc[0])
    cdate = row["close_date"].iloc[0]

    if math.isnan(prev) or prev == 0.0:
        day_chg = float("nan")
    else:
        day_chg = round((last / prev - 1) * 100, 2)

    return {
        "last_close":     last,
        "prev_close":     prev,
        "close_date":     cdate,
        "day_change_pct": day_chg,
    }
