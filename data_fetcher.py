"""
data_fetcher.py  —  MFM v4
All yfinance data retrieval + technical indicator computation.
New in v4:
  • Batched RS cache download (50 tickers/call → ~10× faster)
  • Background RS cache builder with JSON progress file
  • BSE (.BO) ticker support throughout
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os, json, threading, warnings

warnings.filterwarnings("ignore")

CACHE_FILE            = "rs_cache.parquet"
CACHE_TTL_DAYS        = 7
RS_BUILD_STATUS_FILE  = "rs_build_status.json"

# ─────────────────────────────────────────────────────────────────────
#  STATUS FILE HELPERS  (shared between main thread and bg thread)
# ─────────────────────────────────────────────────────────────────────

def _write_status(running: bool, total: int, done: int, current: str, error: str = ""):
    try:
        with open(RS_BUILD_STATUS_FILE, "w") as f:
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


def get_build_status() -> dict:
    """Read RS cache build progress. Returns default if file absent."""
    default = {"running": False, "total": 0, "done": 0, "current": "", "error": ""}
    if not os.path.exists(RS_BUILD_STATUS_FILE):
        return default
    try:
        with open(RS_BUILD_STATUS_FILE) as f:
            return {**default, **json.load(f)}
    except Exception:
        return default


def is_build_running() -> bool:
    return get_build_status().get("running", False)


# ─────────────────────────────────────────────────────────────────────
#  TECHNICAL INDICATORS
# ─────────────────────────────────────────────────────────────────────

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    try:
        high  = df["High"].astype(float)
        low   = df["Low"].astype(float)
        close = df["Close"].astype(float)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low  - close.shift(1)).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        up   = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        alpha = 1 / period
        atr      = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_di  = 100 * pd.Series(plus_dm,  index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr
        dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        val = adx.dropna().iloc[-1]
        return round(float(val), 2) if not np.isnan(val) else 20.0
    except Exception:
        return 20.0


def calc_up_down_vol_ratio(df: pd.DataFrame, lookback: int = 20) -> float:
    try:
        recent = df.tail(lookback).copy()
        recent["up"] = recent["Close"] > recent["Close"].shift(1)
        up_vol   = recent.loc[recent["up"] == True,  "Volume"].mean()
        down_vol = recent.loc[recent["up"] == False, "Volume"].mean()
        if pd.isna(down_vol) or down_vol == 0:
            return 2.0
        ratio = up_vol / down_vol
        return round(float(ratio), 2) if not np.isnan(ratio) else 1.0
    except Exception:
        return 1.0


def calc_breakout_vol_mult(df: pd.DataFrame, lookback: int = 20) -> float:
    try:
        avg50 = df["Volume"].tail(50).mean()
        peak  = df["Volume"].tail(lookback).max()
        if avg50 == 0:
            return 1.0
        return round(float(peak / avg50), 2)
    except Exception:
        return 1.0


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


# ─────────────────────────────────────────────────────────────────────
#  RETURNS CALCULATOR
# ─────────────────────────────────────────────────────────────────────

def _period_return(series: pd.Series, days_ago: int, skip_last_month: bool = False) -> float:
    try:
        end_idx   = -22 if skip_last_month else -1
        start_idx = -(days_ago + (22 if skip_last_month else 0))
        prices = series.iloc[start_idx:end_idx if end_idx != 0 else None]
        if len(prices) < 2:
            return 0.0
        ret = (prices.iloc[-1] / prices.iloc[0] - 1) * 100
        return round(float(ret), 2)
    except Exception:
        return 0.0


def _calc_12m_return(close: pd.Series) -> float:
    """12-month return with 1-month skip (classic momentum)."""
    try:
        if len(close) < 252:
            return 0.0
        return round(float((close.iloc[-22] / close.iloc[-252] - 1) * 100), 2)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────
#  EARNINGS DATA
# ─────────────────────────────────────────────────────────────────────

def _get_earnings_metrics(ticker_obj) -> tuple[float, float, float]:
    eps_growth = rev_growth = surprise = 0.0
    try:
        fin = ticker_obj.quarterly_financials
        if fin is not None and not fin.empty:
            rev_row = None
            for label in ["Total Revenue", "Revenue", "Net Revenue"]:
                if label in fin.index:
                    rev_row = fin.loc[label]; break
            if rev_row is not None and len(rev_row) >= 5:
                latest = rev_row.iloc[0]; year_ago = rev_row.iloc[4]
                if year_ago and year_ago != 0:
                    rev_growth = round((latest / year_ago - 1) * 100, 2)
    except Exception:
        pass
    try:
        earn = ticker_obj.quarterly_earnings
        if earn is not None and not earn.empty and "Earnings" in earn.columns:
            e = earn["Earnings"].dropna()
            if len(e) >= 5:
                eps_growth = round((e.iloc[0] / e.iloc[4] - 1) * 100, 2) if e.iloc[4] != 0 else 0.0
            if "Estimate" in earn.columns:
                est = earn["Estimate"].dropna(); act = earn["Earnings"].dropna()
                if len(est) > 0 and len(act) > 0 and est.iloc[0] != 0:
                    surprise = round((act.iloc[0] - est.iloc[0]) / abs(est.iloc[0]) * 100, 2)
            else:
                if len(e) >= 2 and e.iloc[1] != 0:
                    surprise = round((e.iloc[0] / e.iloc[1] - 1) * 100, 2)
    except Exception:
        pass
    eps_growth = max(-200.0, min(500.0, eps_growth))
    rev_growth = max(-100.0, min(200.0, rev_growth))
    surprise   = max(-100.0, min(100.0, surprise))
    return eps_growth, rev_growth, surprise


# ─────────────────────────────────────────────────────────────────────
#  RS RANK CACHE
# ─────────────────────────────────────────────────────────────────────

def _cache_is_fresh() -> bool:
    if not os.path.exists(CACHE_FILE):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
    return (datetime.now() - mtime).days < CACHE_TTL_DAYS


def get_rs_rank(ticker: str, ret_12m: float) -> float:
    """Percentile rank of this ticker's 12M return vs universe cache."""
    if not os.path.exists(CACHE_FILE):
        return 50.0
    try:
        cache = pd.read_parquet(CACHE_FILE)
        universe_returns = cache["ret_12m"].dropna().values
        if len(universe_returns) == 0:
            return 50.0
        rank = float(np.sum(universe_returns < ret_12m) / len(universe_returns) * 100)
        return round(rank, 1)
    except Exception:
        return 50.0


# ── Batched download  ────────────────────────────────────────────────

def _batch_download_closes(
    tickers: list[str],
    start: datetime,
    end: datetime,
    batch_size: int = 50,
) -> dict[str, pd.Series]:
    """
    Download closing prices for many tickers using batched yfinance calls.
    Returns {ticker: close_series} for tickers with enough data.
    ~10× faster than downloading one-by-one.
    """
    closes: dict[str, pd.Series] = {}

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        if not batch:
            continue
        try:
            if len(batch) == 1:
                df = yf.download(batch[0], start=start, end=end, auto_adjust=True, progress=False)
                if not df.empty and len(df) >= 100:
                    c = df["Close"]
                    if isinstance(c, pd.DataFrame):
                        c = c.iloc[:, 0]
                    closes[batch[0]] = c.squeeze()
            else:
                df = yf.download(
                    batch, start=start, end=end, auto_adjust=True, progress=False, group_by="ticker"
                )
                if df.empty:
                    continue
                # With group_by='ticker', columns are (ticker, field)
                if isinstance(df.columns, pd.MultiIndex):
                    for ticker in batch:
                        try:
                            c = df[ticker]["Close"].dropna()
                            if len(c) >= 100:
                                closes[ticker] = c
                        except Exception:
                            pass
                else:
                    # Single-ticker case even when list was passed
                    c = df["Close"].squeeze()
                    if len(c) >= 100:
                        closes[batch[0]] = c
        except Exception:
            continue

    return closes


def build_rs_cache(tickers: list[str], progress_callback=None) -> pd.DataFrame:
    """
    Original one-by-one downloader — kept for compatibility.
    Calls build_rs_cache_batched internally.
    """
    return build_rs_cache_batched(tickers, progress_callback=progress_callback)


def build_rs_cache_batched(
    tickers: list[str],
    batch_size: int = 50,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Download 13 months of daily closes for all tickers (batched) and
    compute 12M momentum returns. Saves to rs_cache.parquet.

    progress_callback(done: int, total: int, current: str)
    """
    end   = datetime.now()
    start = end - timedelta(days=400)
    total = len(tickers)

    # NIFTY 50 benchmark
    nifty_12m = 0.0
    try:
        nifty_df    = yf.download("^NSEI", start=start, end=end, auto_adjust=True, progress=False)
        nifty_close = nifty_df["Close"].squeeze()
        if len(nifty_close) > 252:
            nifty_12m = float((nifty_close.iloc[-22] / nifty_close.iloc[-252] - 1) * 100)
    except Exception:
        pass

    results = []
    done    = 0

    for i in range(0, total, batch_size):
        batch = tickers[i : i + batch_size]
        batch_label = batch[0] if batch else "…"
        if progress_callback:
            progress_callback(done, total, batch_label)

        try:
            if len(batch) == 1:
                df = yf.download(batch[0], start=start, end=end, auto_adjust=True, progress=False)
                if not df.empty:
                    c = df["Close"].squeeze()
                    if len(c) >= 252:
                        ret = _calc_12m_return(c)
                        results.append({"ticker": batch[0], "ret_12m": ret})
                done += 1
            else:
                df = yf.download(
                    batch, start=start, end=end, auto_adjust=True, progress=False, group_by="ticker"
                )
                if df.empty:
                    done += len(batch)
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    for ticker in batch:
                        try:
                            c = df[ticker]["Close"].dropna()
                            if len(c) >= 252:
                                ret = _calc_12m_return(c)
                                results.append({"ticker": ticker, "ret_12m": ret})
                        except Exception:
                            pass
                        done += 1
                        if progress_callback:
                            progress_callback(done, total, ticker)
                else:
                    # Returned as single-ticker frame
                    c = df["Close"].squeeze()
                    if len(c) >= 252:
                        ret = _calc_12m_return(c)
                        results.append({"ticker": batch[0], "ret_12m": ret})
                    done += len(batch)
        except Exception:
            done += len(batch)
            if progress_callback:
                progress_callback(done, total, f"skip_batch_{i}")

    cache_df = pd.DataFrame(results)
    if not cache_df.empty:
        cache_df["nifty_12m"] = nifty_12m
        cache_df.to_parquet(CACHE_FILE, index=False)
    if progress_callback:
        progress_callback(total, total, "done")
    return cache_df


# ── Background build  ────────────────────────────────────────────────

_bg_lock   = threading.Lock()
_bg_thread: threading.Thread | None = None


def start_background_cache_build(tickers: list[str]) -> bool:
    """
    Spawn a daemon thread to build the RS cache in the background.
    Returns True if started, False if already running.
    Writes progress to RS_BUILD_STATUS_FILE for the app to poll.
    """
    global _bg_thread
    with _bg_lock:
        if _bg_thread is not None and _bg_thread.is_alive():
            return False  # already running

        _write_status(running=True, total=len(tickers), done=0, current="Initialising…")

        def _worker():
            try:
                def _cb(done, total, current):
                    _write_status(running=True, total=total, done=done, current=current)

                build_rs_cache_batched(tickers, progress_callback=_cb)
                _write_status(running=False, total=len(tickers), done=len(tickers), current="✦ Complete")
            except Exception as e:
                _write_status(running=False, total=len(tickers), done=0, current="", error=str(e))

        _bg_thread = threading.Thread(target=_worker, daemon=True)
        _bg_thread.start()
        return True


# ─────────────────────────────────────────────────────────────────────
#  MAIN DATA FETCH  (single stock)
# ─────────────────────────────────────────────────────────────────────

def fetch_all_data(symbol: str) -> dict:
    """
    Fetches all data for MFM scoring of a single stock.
    Works for both .NS (NSE) and .BO (BSE) tickers.
    """
    try:
        end   = datetime.now()
        start = end - timedelta(days=400)

        df = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
        if df.empty or len(df) < 60:
            return {"status": f"Insufficient price history for {symbol}. Verify the ticker is correct."}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        volume = df["Volume"].squeeze()

        # Returns
        ret_12m_skip = _period_return(close, 252, skip_last_month=True)
        ret_6m_skip  = _period_return(close, 126, skip_last_month=True)
        ret_3m       = _period_return(close, 63,  skip_last_month=False)
        ret_1m       = _period_return(close, 22,  skip_last_month=False)

        # Benchmark — use NIFTY 50 for NSE, SENSEX for BSE
        bench_sym = "^BSESN" if symbol.endswith(".BO") else "^NSEI"
        nifty_df  = yf.download(bench_sym, start=start, end=end, auto_adjust=True, progress=False)
        if isinstance(nifty_df.columns, pd.MultiIndex):
            nifty_df.columns = nifty_df.columns.get_level_values(0)
        nifty_close = nifty_df["Close"].squeeze() if not nifty_df.empty else None

        nifty_12m = _period_return(nifty_close, 252, skip_last_month=True) if nifty_close is not None else 0.0
        nifty_6m  = _period_return(nifty_close, 126, skip_last_month=True) if nifty_close is not None else 0.0

        rs_12m = round(ret_12m_skip - nifty_12m, 2)
        rs_6m  = round(ret_6m_skip  - nifty_6m,  2)
        rs_rank = get_rs_rank(symbol, ret_12m_skip)

        # Trend
        ema200 = calc_ema(close, 200)
        ema50  = calc_ema(close, 50)
        cmp    = float(close.iloc[-1])
        pct_above_200ema  = round((cmp / float(ema200.iloc[-1]) - 1) * 100, 2)
        pct_above_50ema   = round((cmp / float(ema50.iloc[-1])  - 1) * 100, 2)
        high52 = float(high.iloc[-252:].max() if len(high) >= 252 else high.max())
        pct_from_52w_high = round((cmp / high52 - 1) * 100, 2)
        adx = calc_adx(df)

        # Volume
        avg20 = float(volume.tail(20).mean())
        avg50 = float(volume.tail(50).mean())
        vol_ratio = round(avg20 / avg50, 2) if avg50 > 0 else 1.0
        ud_ratio  = calc_up_down_vol_ratio(df)
        bv_mult   = calc_breakout_vol_mult(df)

        # Info
        ticker_obj = yf.Ticker(symbol)
        info = {}
        try:
            info = ticker_obj.info or {}
        except Exception:
            pass

        name      = info.get("longName") or info.get("shortName") or symbol
        sector    = info.get("sector") or "N/A"
        mkt_cap   = info.get("marketCap")
        curr_price = info.get("currentPrice") or info.get("regularMarketPrice") or cmp
        prev_close = info.get("previousClose") or (float(close.iloc[-2]) if len(close) > 1 else cmp)
        day_change = round((curr_price / prev_close - 1) * 100, 2) if prev_close else 0.0

        if mkt_cap:
            if mkt_cap >= 2e11:   mkt_cap_label = "Large Cap"
            elif mkt_cap >= 5e10: mkt_cap_label = "Mid Cap"
            else:                 mkt_cap_label = "Small Cap"
        else:
            mkt_cap_label = "N/A"

        # Earnings
        eps_growth, rev_growth, surprise = _get_earnings_metrics(ticker_obj)
        rsi14 = calc_rsi(close, 14)

        # Currency symbol
        currency = "₹" if symbol.endswith((".NS", ".BO")) else "$"

        return {
            "status":        "ok",
            "symbol":        symbol,
            "name":          name,
            "sector":        sector,
            "cmp":           round(float(curr_price), 2),
            "day_change":    day_change,
            "mkt_cap_label": mkt_cap_label,
            "currency":      currency,
            "exchange":      "BSE" if symbol.endswith(".BO") else "NSE",
            "history":       df,
            "ema200":        ema200,
            "ema50":         ema50,
            "rsi14":         rsi14,
            "metrics": {
                "ret_12m":           ret_12m_skip,
                "ret_6m":            ret_6m_skip,
                "ret_3m":            ret_3m,
                "ret_1m":            ret_1m,
                "rs_12m":            rs_12m,
                "rs_6m":             rs_6m,
                "rs_rank":           rs_rank,
                "pct_above_200ema":  pct_above_200ema,
                "pct_above_50ema":   pct_above_50ema,
                "pct_from_52w_high": pct_from_52w_high,
                "adx":               adx,
                "vol_ratio_20_50":   vol_ratio,
                "up_down_vol_ratio": ud_ratio,
                "breakout_vol_mult": bv_mult,
                "eps_growth_yoy":    eps_growth,
                "rev_growth_yoy":    rev_growth,
                "earnings_surprise": surprise,
            },
        }

    except Exception as e:
        return {"status": f"Error fetching data for {symbol}: {str(e)}"}


# ─────────────────────────────────────────────────────────────────────
#  BATCH SCREENER
# ─────────────────────────────────────────────────────────────────────

def fetch_batch_scores(tickers: list[str], progress_callback=None) -> list[dict]:
    from mfm_engine import (
        score_price_momentum, score_relative_strength, score_trend_quality,
        score_volume_momentum, score_earnings_momentum, compute_composite,
    )
    results = []
    for i, sym in enumerate(tickers):
        if progress_callback:
            progress_callback(i, len(tickers), sym)
        data = fetch_all_data(sym)
        if data.get("status") != "ok":
            continue
        m = data["metrics"]
        factors = {
            "price_momentum":    score_price_momentum(m["ret_12m"], m["ret_6m"], m["ret_3m"], m["ret_1m"]),
            "relative_strength": score_relative_strength(m["rs_12m"], m["rs_6m"], m["rs_rank"]),
            "trend_quality":     score_trend_quality(m["pct_above_200ema"], m["pct_above_50ema"], m["pct_from_52w_high"], m["adx"]),
            "volume_momentum":   score_volume_momentum(m["vol_ratio_20_50"], m["up_down_vol_ratio"], m["breakout_vol_mult"]),
            "earnings_momentum": score_earnings_momentum(m["eps_growth_yoy"], m["rev_growth_yoy"], m["earnings_surprise"]),
        }
        comp = compute_composite(factors)
        tc   = sym.replace(".NS", "").replace(".BO", "")
        exch = "BSE" if sym.endswith(".BO") else "NSE"
        results.append({
            "Ticker":      tc,
            "Exchange":    exch,
            "Name":        data["name"],
            "Sector":      data["sector"],
            "CMP (₹)":     data["cmp"],
            "Day (%)":     data["day_change"],
            "Composite":   comp["composite"],
            "Signal":      comp["signal"]["label"],
            "Price Mom":   factors["price_momentum"]["score"],
            "Rel Strength": factors["relative_strength"]["score"],
            "Trend Quality": factors["trend_quality"]["score"],
            "Earnings Mom": factors["earnings_momentum"]["score"],
            "Volume Mom":  factors["volume_momentum"]["score"],
            "12M Ret (%)": m["ret_12m"],
            "RS Rank":     m["rs_rank"],
            "ADX":         m["adx"],
        })
    results.sort(key=lambda x: x["Composite"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────
#  MARKET REGIME
# ─────────────────────────────────────────────────────────────────────

def fetch_market_regime() -> dict:
    try:
        end   = datetime.now()
        start = end - timedelta(days=400)
        df    = yf.download("^NSEI", start=start, end=end, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].squeeze()
        if len(close) < 50:
            return {"label": "UNKNOWN", "color": "#9e9080", "cmp": 0}

        cmp       = float(close.iloc[-1])
        e200      = float(calc_ema(close, 200).iloc[-1])
        e50       = float(calc_ema(close, 50).iloc[-1])
        e200_prev = float(calc_ema(close, 200).iloc[-21])
        slope200  = (e200 / e200_prev - 1) * 100

        above_200  = cmp > e200
        above_50   = cmp > e50
        rising_200 = slope200 > 0

        mom_1m = float((close.iloc[-1] / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0
        mom_3m = float((close.iloc[-1] / close.iloc[-63] - 1) * 100) if len(close) >= 63 else 0

        if above_200 and above_50 and rising_200:
            label, color = "BULL MARKET", "#6bbf72"
        elif above_200 and above_50 and not rising_200:
            label, color = "BULL (LATE STAGE)", "#c9a84c"
        elif above_200 and not above_50:
            label, color = "CAUTION", "#c49a2a"
        elif not above_200 and above_50:
            label, color = "RECOVERY WATCH", "#c49a2a"
        else:
            label, color = "BEAR MARKET", "#c05a5a"

        return {
            "label": label, "color": color, "cmp": round(cmp, 1),
            "pct_200": round((cmp / e200 - 1) * 100, 2),
            "pct_50":  round((cmp / e50  - 1) * 100, 2),
            "slope_200": round(slope200, 2),
            "mom_1m": round(mom_1m, 2),
            "mom_3m": round(mom_3m, 2),
        }
    except Exception:
        return {"label": "UNKNOWN", "color": "#9e9080", "cmp": 0}
