# MFM Momentum Engine — v4

Multi-Factor Momentum scoring for **all NSE + BSE listed equities** (~5,000+ stocks).

## Quick Start

```bash
pip install -r requirements.txt
streamlit run app.py
```

## What's New in v4

| Feature | v3 | v4 |
|---|---|---|
| Universe | NIFTY 500 only (~400 hardcoded) | Full NSE (~2,000) + BSE (~5,000) |
| RS Rank percentile | Against 400 stocks | Against full NSE+BSE universe |
| Autocomplete | ~200 names | ~7,000 entries with (NSE)/(BSE) suffix |
| RS Cache build | Foreground, blocks UI | **Background thread** — app stays usable |
| Download speed | One ticker at a time | Batched 50 tickers/call (~10× faster) |
| BSE support | Partial | Full `.BO` ticker support throughout |

## Data Sources

**Universe** (refreshed every 30 days):
- NSE: `archives.nseindia.com/content/equities/EQUITY_L.csv` — full equity master
- BSE: `api.bseindia.com` ListofScripData API — all active equity scrips
- Fallback: 400+ NSE + 100 BSE-only hardcoded tickers

**Price data**: Yahoo Finance (`yfinance`) via `.NS` (NSE) and `.BO` (BSE) suffixes

## Sidebar Controls

| Section | What it does |
|---|---|
| Refresh Universe | Re-fetches NSE + BSE equity masters. Run after quarterly rebalances. |
| Build / Refresh RS Cache | Starts background download of 13M price history for all stocks. Takes ~8–15 min; app stays usable. Check status with "Refresh Status" button. |

## RS Cache Build

The RS Rank percentile score requires downloading 12 months of price history for every stock
in the universe (~5,000 tickers). This runs in the background so you can continue using the app.

Progress is tracked in `rs_build_status.json` — click "Refresh Status" in the sidebar to update.

## Searching BSE Stocks

In the search box, you can type:
- **NSE symbol**: `RELIANCE`, `LUMAXTECH`
- **BSE scrip code**: `500325` (Reliance on BSE)
- **Company name**: `"Reli"`, `"Bajaj"`, `"HDFC"`

The autocomplete will show both exchanges where applicable:
```
Reliance Industries  →  RELIANCE (NSE)
Reliance Industries  →  500325 (BSE)
```

## Scoring Weights

| Factor | Weight | Key Metrics |
|---|---|---|
| Price Momentum | 30% | 12M (skip-1), 6M, 3M, 1M returns |
| Relative Strength | 25% | Excess return vs benchmark + percentile rank |
| Trend Quality | 20% | % above 200/50 EMA, distance from 52W high, ADX |
| Earnings Momentum | 15% | EPS growth YoY, Revenue growth, Earnings surprise |
| Volume Momentum | 10% | 20D/50D volume ratio, Up/Down volume, Breakout multiplier |

## Benchmark

- **NSE stocks** (`.NS`): benchmarked against NIFTY 50 (`^NSEI`)  
- **BSE stocks** (`.BO`): benchmarked against SENSEX (`^BSESN`)
