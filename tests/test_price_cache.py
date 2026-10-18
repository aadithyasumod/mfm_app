"""
tests/test_price_cache.py
Unit tests for price_cache.py — staleness logic, bhavcopy fetch, get_price(), NaN guards.
"""
import sys, os, math, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_workdir(tmp_path, monkeypatch):
    """Run every test in a fresh temp directory so file artifacts don't bleed."""
    monkeypatch.chdir(tmp_path)


def _make_cache(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ticker", "last_close", "prev_close", "close_date"])


def _make_bhavcopy_csv(rows: list[dict]) -> str:
    """
    Build a CSV string matching the NSE sec_bhavdata_full format:
    SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,
    LAST_PRICE,CLOSE_PRICE,AVG_PRICE,TTL_TRD_QNTY,TURNOVER_LACS,
    NO_OF_TRADES,DELIV_QTY,DELIV_PER
    """
    lines = [
        "SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,"
        "LAST_PRICE,CLOSE_PRICE,AVG_PRICE,TTL_TRD_QNTY,TURNOVER_LACS,"
        "NO_OF_TRADES,DELIV_QTY,DELIV_PER"
    ]
    for r in rows:
        lines.append(
            f"{r['SYMBOL']},{r.get('SERIES','EQ')},02-Apr-2026,"
            f"{r['PREV_CLOSE']},{r.get('OPEN_PRICE',100)},{r.get('HIGH_PRICE',110)},"
            f"{r.get('LOW_PRICE',90)},{r['CLOSE_PRICE']},{r['CLOSE_PRICE']},"
            f"1200.5,1000000,5000000.0,50000,800000,80.0"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
#  _last_weekday
# ─────────────────────────────────────────────────────────────────────

def test_last_weekday_saturday_returns_friday():
    from price_cache import _last_weekday
    sat = date(2026, 4, 4)
    assert _last_weekday(sat) == date(2026, 4, 3)


def test_last_weekday_sunday_returns_friday():
    from price_cache import _last_weekday
    sun = date(2026, 4, 5)
    assert _last_weekday(sun) == date(2026, 4, 3)


def test_last_weekday_weekday_unchanged():
    from price_cache import _last_weekday
    wed = date(2026, 4, 1)
    assert _last_weekday(wed) == wed


# ─────────────────────────────────────────────────────────────────────
#  is_price_cache_stale
# ─────────────────────────────────────────────────────────────────────

def test_stale_when_no_file():
    from price_cache import is_price_cache_stale
    assert is_price_cache_stale() is True


def test_fresh_when_cache_has_todays_close(tmp_path):
    """Cache is fresh when close_date == today and time is after 19:30 IST."""
    from price_cache import is_price_cache_stale, PRICE_CACHE_FILE
    from zoneinfo import ZoneInfo

    today = date.today()
    df = _make_cache([{"ticker": "TEST.NS", "last_close": 100.0,
                       "prev_close": 99.0, "close_date": today}])
    df.to_parquet(PRICE_CACHE_FILE, index=False)

    IST = ZoneInfo("Asia/Kolkata")
    fake_now = datetime(today.year, today.month, today.day, 20, 0, tzinfo=IST)
    if today.weekday() >= 5:
        pytest.skip("Skipping on weekends")

    with patch("price_cache.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.fromtimestamp = datetime.fromtimestamp
        mock_dt.fromisoformat  = datetime.fromisoformat
        result = is_price_cache_stale()

    assert result is False


def test_stale_when_close_date_is_yesterday(tmp_path):
    """Cache is stale when close_date is the day before the target date."""
    from price_cache import is_price_cache_stale, PRICE_CACHE_FILE
    from zoneinfo import ZoneInfo

    yesterday = date.today() - timedelta(days=1)
    df = _make_cache([{"ticker": "TEST.NS", "last_close": 100.0,
                       "prev_close": 99.0, "close_date": yesterday}])
    df.to_parquet(PRICE_CACHE_FILE, index=False)

    today = date.today()
    if today.weekday() >= 5:
        pytest.skip("Skipping on weekends")

    IST = ZoneInfo("Asia/Kolkata")
    fake_now = datetime(today.year, today.month, today.day, 20, 0, tzinfo=IST)

    with patch("price_cache.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.fromtimestamp = datetime.fromtimestamp
        mock_dt.fromisoformat  = datetime.fromisoformat
        result = is_price_cache_stale()

    assert result is True


# ─────────────────────────────────────────────────────────────────────
#  _fetch_nse_bhavcopy
# ─────────────────────────────────────────────────────────────────────

def test_fetch_nse_bhavcopy_parses_eq_rows(tmp_path):
    """Valid bhavcopy CSV returns a DataFrame with .NS tickers and correct prices."""
    from price_cache import _fetch_nse_bhavcopy

    csv_text = _make_bhavcopy_csv([
        {"SYMBOL": "RELIANCE",  "CLOSE_PRICE": 1350.5, "PREV_CLOSE": 1369.2},
        {"SYMBOL": "TCS",       "CLOSE_PRICE": 2408.2, "PREV_CLOSE": 2375.1},
        {"SYMBOL": "IGNOREME",  "CLOSE_PRICE": 500.0,  "PREV_CLOSE": 490.0, "SERIES": "BE"},
    ])

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.headers = {"content-type": "text/csv"}
    mock_resp.text = csv_text

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with patch("price_cache._nse_session", return_value=mock_session):
        result = _fetch_nse_bhavcopy(date(2026, 4, 2))

    assert result is not None
    tickers = result["ticker"].tolist()
    assert "RELIANCE.NS" in tickers
    assert "TCS.NS" in tickers
    # BE series must be excluded
    assert "IGNOREME.NS" not in tickers

    rel = result[result["ticker"] == "RELIANCE.NS"].iloc[0]
    assert rel["last_close"] == pytest.approx(1350.5)
    assert rel["prev_close"] == pytest.approx(1369.2)
    assert rel["close_date"] == date(2026, 4, 2)


def test_fetch_nse_bhavcopy_returns_none_on_404():
    """HTTP 404 (holiday or future date) returns None without raising."""
    from price_cache import _fetch_nse_bhavcopy

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 404

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with patch("price_cache._nse_session", return_value=mock_session):
        result = _fetch_nse_bhavcopy(date(2026, 4, 3))

    assert result is None


def test_fetch_nse_bhavcopy_returns_none_on_html_response():
    """HTML content-type (NSE redirect/error page) returns None."""
    from price_cache import _fetch_nse_bhavcopy

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.content = b"<html>NSE error</html>"

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with patch("price_cache._nse_session", return_value=mock_session):
        result = _fetch_nse_bhavcopy(date(2026, 4, 3))

    assert result is None


# ─────────────────────────────────────────────────────────────────────
#  get_price
# ─────────────────────────────────────────────────────────────────────

def test_get_price_normal():
    from price_cache import get_price
    cache = _make_cache([
        {"ticker": "RELIANCE.NS", "last_close": 2960.0, "prev_close": 2900.0,
         "close_date": date(2026, 4, 1)},
    ])
    result = get_price("RELIANCE.NS", cache_df=cache)

    assert result["last_close"] == 2960.0
    assert result["prev_close"] == 2900.0
    assert result["close_date"] == date(2026, 4, 1)
    assert pytest.approx(result["day_change_pct"], rel=1e-3) == (2960.0 / 2900.0 - 1) * 100


def test_get_price_nan_prev_close():
    from price_cache import get_price
    cache = _make_cache([
        {"ticker": "NEWIPO.NS", "last_close": 500.0, "prev_close": float("nan"),
         "close_date": date(2026, 4, 1)},
    ])
    result = get_price("NEWIPO.NS", cache_df=cache)

    assert result["last_close"] == 500.0
    assert math.isnan(result["day_change_pct"])


def test_get_price_zero_prev_close():
    from price_cache import get_price
    cache = _make_cache([
        {"ticker": "ZERO.NS", "last_close": 10.0, "prev_close": 0.0,
         "close_date": date(2026, 4, 1)},
    ])
    result = get_price("ZERO.NS", cache_df=cache)
    assert math.isnan(result["day_change_pct"])


def test_get_price_missing_ticker():
    from price_cache import get_price
    cache = _make_cache([
        {"ticker": "RELIANCE.NS", "last_close": 2960.0, "prev_close": 2900.0,
         "close_date": date(2026, 4, 1)},
    ])
    result = get_price("GHOST.NS", cache_df=cache)

    assert math.isnan(result["last_close"])
    assert math.isnan(result["prev_close"])
    assert result["close_date"] is None
    assert math.isnan(result["day_change_pct"])


# ─────────────────────────────────────────────────────────────────────
#  load_price_cache
# ─────────────────────────────────────────────────────────────────────

def test_load_price_cache_missing_file():
    from price_cache import load_price_cache
    df = load_price_cache()
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["ticker", "last_close", "prev_close", "close_date"]
    assert len(df) == 0


def test_load_price_cache_reads_parquet(tmp_path):
    from price_cache import load_price_cache, PRICE_CACHE_FILE
    expected = _make_cache([
        {"ticker": "TCS.NS", "last_close": 3500.0, "prev_close": 3480.0,
         "close_date": date(2026, 4, 1)},
    ])
    expected.to_parquet(PRICE_CACHE_FILE, index=False)

    result = load_price_cache()
    assert len(result) == 1
    assert result.iloc[0]["ticker"] == "TCS.NS"


# ─────────────────────────────────────────────────────────────────────
#  is_price_build_running
# ─────────────────────────────────────────────────────────────────────

def test_not_running_when_no_status_file():
    from price_cache import is_price_build_running
    assert is_price_build_running() is False


def test_running_when_status_file_says_running(tmp_path):
    import json
    from price_cache import is_price_build_running, PRICE_STATUS_FILE
    with open(PRICE_STATUS_FILE, "w") as f:
        json.dump({"running": True, "total": 100, "done": 10, "current": "TEST.NS"}, f)
    assert is_price_build_running() is True
