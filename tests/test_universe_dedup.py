"""
tests/test_universe_dedup.py
Unit tests for ISIN-based deduplication in universe_builder._merge_exchanges().
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from universe_builder import _merge_exchanges


def _nse_row(symbol, isin=""):
    return {
        "ticker_yf": symbol + ".NS",
        "display_name": symbol + " (NSE)",
        "company_name": symbol,
        "exchange": "NSE",
        "symbol": symbol,
        "isin": isin,
        "industry": "Test",
    }


def _bse_row(scrip, company, isin=""):
    return {
        "ticker_yf": scrip + ".BO",
        "display_name": company + " (BSE)",
        "company_name": company,
        "exchange": "BSE",
        "symbol": scrip,
        "isin": isin,
        "industry": "Test",
    }


def test_dedup_prefers_nse_for_same_isin():
    """When NSE and BSE share an ISIN, only the NSE ticker should remain."""
    nse = pd.DataFrame([_nse_row("RELIANCE", isin="INE002A01018")])
    bse = pd.DataFrame([_bse_row("500325", "Reliance Industries", isin="INE002A01018")])

    result = _merge_exchanges(nse, bse)

    tickers = result["ticker_yf"].tolist()
    assert "RELIANCE.NS" in tickers, "NSE ticker should be kept"
    assert "500325.BO" not in tickers, "BSE ticker should be dropped (same ISIN)"
    assert len(result) == 1


def test_dedup_keeps_bse_only_stock():
    """BSE-only stocks (unique ISIN not on NSE) should be kept."""
    nse = pd.DataFrame([_nse_row("TCS", isin="INE467B01029")])
    bse = pd.DataFrame([
        _bse_row("532540", "Tata Consultancy Services", isin="INE467B01029"),  # same → drop
        _bse_row("543232", "BSE Only Corp", isin="INE999X01000"),              # unique → keep
    ])

    result = _merge_exchanges(nse, bse)

    tickers = result["ticker_yf"].tolist()
    assert "TCS.NS" in tickers
    assert "532540.BO" not in tickers  # dropped — same ISIN as TCS.NS
    assert "543232.BO" in tickers      # kept — unique BSE-only ISIN
    assert len(result) == 2


def test_dedup_handles_no_isin_rows():
    """Rows with no ISIN should not be dropped by ISIN dedup."""
    nse = pd.DataFrame([_nse_row("SMALLCO", isin="")])
    bse = pd.DataFrame([_bse_row("999999", "Small Corp BSE", isin="")])

    result = _merge_exchanges(nse, bse)

    tickers = result["ticker_yf"].tolist()
    assert "SMALLCO.NS" in tickers
    assert "999999.BO" in tickers
    assert len(result) == 2


def test_dedup_nse_only_input():
    """When only NSE data is provided, all rows are returned."""
    nse = pd.DataFrame([_nse_row("INFY", "INE009A01021"), _nse_row("WIPRO", "INE075A01022")])

    result = _merge_exchanges(nse, None)

    assert len(result) == 2
    assert set(result["exchange"].tolist()) == {"NSE"}


def test_dedup_no_duplicate_ticker_yf():
    """Result must never have duplicate ticker_yf values."""
    nse = pd.DataFrame([
        _nse_row("HDFC", isin="INE001A01036"),
        _nse_row("HDFC", isin="INE001A01036"),  # accidental duplicate
    ])
    bse = pd.DataFrame([_bse_row("500010", "HDFC", isin="INE001A01036")])

    result = _merge_exchanges(nse, bse)

    assert result["ticker_yf"].is_unique, "ticker_yf must be unique in output"
