"""
rs_cache_builder.py
===================
Background RS cache builder using batched yfinance downloads.
Writes progress to rs_cache_progress.json so the Streamlit UI
can poll status without blocking.

Usage:
    from rs_cache_builder import start_background_build, get_progress, is_running
    start_background_build(tickers)          # non-blocking
    progress = get_progress()                # {pct, done, total, status, eta_min}
"""

import os, json, threading, time
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

CACHE_FILE    = "rs_cache.parquet"
PROGRESS_FILE = "rs_cache_progress.json"
BATCH_SIZE    = 80     # tickers per yfinance.download() call
MAX_WORKERS   = 4      # parallel batch threads

_build_thread: threading.Thread | None = None
_stop_flag = threading.Event()

# ─────────────────────────────────────────────────────────────
#  Progress file helpers
# ─────────────────────────────────────────────────────────────
def _write_progress(done: int, total: int, status: str, eta_min: float = 0.0):
    data = {
        "done": done, "total": total,
        "pct": int(done / max(total, 1) * 100),
        "status": status,
        "eta_min": round(eta_min, 1),
        "ts": datetime.now().isoformat(),
    }
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def get_progress() -> dict:
    if not os.path.exists(PROGRESS_FILE):
        return {"done": 0, "total": 0, "pct": 0, "status": "idle", "eta_min": 0.0, "ts": ""}
    try:
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"done": 0, "total": 0, "pct": 0, "status": "idle", "eta_min": 0.0, "ts": ""}


def is_running() -> bool:
    global _build_thread
    return _build_thread is not None and _build_thread.is_alive()


def cache_is_fresh(max_age_days: int = 7) -> bool:
    if not os.path.exists(CACHE_FILE):
        return False
    age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))).days
    return age < max_age_days


def load_rs_cache() -> pd.Series | None:
    """Returns Series of {ticker: 12m_return} or None if unavailable."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        df = pd.read_parquet(CACHE_FILE)
        if "ret_12m" in df.columns and "ticker" in df.columns:
            return df.set_index("ticker")["ret_12m"]
        return None
    except Exception:
        return None


def get_rs_rank(stock_12m_return: float) -> float:
    """
    Percentile rank of stock_12m_return against cached universe.
    Returns 50.0 as fallback when cache is unavailable.
    """
    cache = load_rs_cache()
    if cache is None or len(cache) < 10:
        return 50.0
    universe = cache.dropna().values
    return float(np.sum(universe < stock_12m_return) / len(universe) * 100)


# ─────────────────────────────────────────────────────────────
#  Core downloader  —  batched parallel fetch
# ─────────────────────────────────────────────────────────────
def _download_batch(tickers: list[str]) -> dict[str, float]:
    """Download 12-month returns for a batch of tickers."""
    results: dict[str, float] = {}
    try:
        end   = datetime.now()
        start = end - timedelta(days=380)
        data  = yf.download(
            tickers,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        if data.empty:
            return results

        # Handle multi-ticker MultiIndex columns
        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"] if "Close" in data.columns.get_level_values(0) else None
        else:
            close = data[["Close"]] if "Close" in data.columns else None

        if close is None:
            return results

        for ticker in tickers:
            try:
                if isinstance(close.columns, pd.Index) and ticker in close.columns:
                    s = close[ticker].dropna()
                elif hasattr(close, "squeeze"):
                    s = close.squeeze().dropna()
                else:
                    continue

                if len(s) < 50:
                    continue

                # 12M skip-1: return from -252 to -22 trading days
                if len(s) >= 252:
                    ret = (s.iloc[-22] / s.iloc[-252] - 1) * 100
                else:
                    ret = (s.iloc[-1] / s.iloc[0] - 1) * 100

                results[ticker] = float(ret)
            except Exception:
                continue

    except Exception:
        # Fallback: individual downloads if batch fails
        for ticker in tickers:
            try:
                s = yf.download(ticker, period="13mo", auto_adjust=True, progress=False)
                if s.empty: continue
                close_col = s["Close"].squeeze()
                if len(close_col) < 50: continue
                if len(close_col) >= 252:
                    ret = (close_col.iloc[-22] / close_col.iloc[-252] - 1) * 100
                else:
                    ret = (close_col.iloc[-1] / close_col.iloc[0] - 1) * 100
                results[ticker] = float(ret)
            except Exception:
                continue

    return results


def _build_worker(tickers: list[str]):
    """Background thread: batch-downloads all tickers and writes cache."""
    total   = len(tickers)
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    results: dict[str, float] = {}

    _write_progress(0, total, "starting", 0)
    t0 = time.time()

    for bi, batch in enumerate(batches):
        if _stop_flag.is_set():
            _write_progress(len(results), total, "cancelled")
            return

        _write_progress(len(results), total,
                        f"Batch {bi+1}/{len(batches)} — downloading {len(batch)} stocks…",
                        eta_min=_eta(t0, len(results), total))

        batch_results = _download_batch(batch)
        results.update(batch_results)

        # Incremental save — makes partial cache available immediately
        _flush_cache(results)

    _write_progress(len(results), total, "complete", 0)


def _eta(t0: float, done: int, total: int) -> float:
    elapsed = time.time() - t0
    if done == 0:
        return 0.0
    rate = done / elapsed        # stocks / second
    remaining = total - done
    return remaining / rate / 60  # minutes


def _flush_cache(results: dict[str, float]):
    """Write current results to parquet (atomic-ish via temp file)."""
    if not results:
        return
    df  = pd.DataFrame({"ticker": list(results.keys()), "ret_12m": list(results.values())})
    tmp = CACHE_FILE + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, CACHE_FILE)


# ─────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────
def start_background_build(tickers: list[str], force: bool = False):
    """
    Starts RS cache build in a background thread.
    Non-blocking — returns immediately.
    Does nothing if already running or cache is fresh (unless force=True).
    """
    global _build_thread, _stop_flag

    if is_running():
        return  # already running

    if not force and cache_is_fresh():
        return  # still fresh

    _stop_flag.clear()
    _build_thread = threading.Thread(
        target=_build_worker,
        args=(tickers,),
        daemon=True,
        name="rs_cache_builder",
    )
    _build_thread.start()


def stop_background_build():
    """Request the background build to stop gracefully."""
    _stop_flag.set()


# Allow running directly for a full manual rebuild
if __name__ == "__main__":
    from universe_builder import load_universe
    df = load_universe()
    nse_tickers = df[df["exchange"] == "NSE"]["ticker_yf"].tolist()
    bse_tickers = df[df["exchange"] == "BSE"]["ticker_yf"].tolist()
    all_tickers = nse_tickers + bse_tickers
    print(f"Building RS cache for {len(all_tickers):,} tickers…")
    _stop_flag.clear()
    _build_worker(all_tickers)
    print("Done.")
