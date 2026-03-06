"""
nse_bse_tickers.py
Dynamic ticker resolution using the universe built by universe_builder.py.

Exposes:
  ALL_TICKERS       — full list of .NS + .BO symbols (for RS ranking universe)
  NSE_TICKERS       — NSE-only tickers
  DISPLAY_LIST      — autocomplete entries: "Company Name (NSE)  ->  TICKER"
  resolve_ticker()  — resolves user query to yfinance symbol
"""

import os, pandas as pd
from universe_builder import load_universe

_df = load_universe()

# ── Ticker lists ──────────────────────────────────────────────────────
ALL_TICKERS = _df["ticker_ns"].tolist()
NSE_TICKERS = _df[_df["exchange"] == "NSE"]["ticker_ns"].tolist()
BSE_TICKERS = _df[_df["exchange"] == "BSE"]["ticker_ns"].tolist()

# Keep backward-compat alias
NIFTY500_TICKERS = NSE_TICKERS  # used by data_fetcher RS cache

# ── Lookup dictionaries ───────────────────────────────────────────────
# company_lower -> ticker_ns  (prefer NSE over BSE when both exist)
_name_map: dict[str,str] = {}
# Also build symbol -> ticker_ns
_sym_map: dict[str,str] = {}

# Process BSE first, then NSE (so NSE overwrites — NSE preferred)
for exch in ("BSE", "NSE"):
    sub = _df[_df["exchange"] == exch]
    for _, row in sub.iterrows():
        name = str(row["company"]).strip().lower()
        sym  = str(row["symbol"]).strip().lower()
        ticker = str(row["ticker_ns"])
        _name_map[name] = ticker
        _sym_map[sym]   = ticker

# Manual short-form aliases (commonly typed names that differ from official)
_ALIASES = {
    "tcs": "TCS.NS", "hul": "HINDUNILVR.NS", "sbi": "SBIN.NS",
    "l&t": "LT.NS", "m&m": "M&M.NS", "hcltech": "HCLTECH.NS",
    "dr reddy": "DRREDDY.NS", "dr reddys": "DRREDDY.NS",
    "bajaj fin": "BAJFINANCE.NS", "bpcl": "BPCL.NS",
    "ongc": "ONGC.NS", "ntpc": "NTPC.NS", "gail": "GAIL.NS",
    "ioc": "IOC.NS", "indian oil": "IOC.NS",
    "pfc": "PFC.NS", "rec": "RECLTD.NS",
    "naukri": "NAUKRI.NS", "dmart": "DMART.NS",
    "dominos": "JUBLFOOD.NS", "domino's": "JUBLFOOD.NS",
    "mcdonalds": "WESTLIFE.NS", "mcdonald's": "WESTLIFE.NS",
    "royal enfield": "EICHERMOT.NS", "jockey": "PAGEIND.NS",
    "fevicol": "PIDILITIND.NS", "taj hotels": "INDHOTEL.NS",
    "taj": "INDHOTEL.NS", "angel broking": "ANGELONE.NS",
    "igl": "IGL.NS", "mgl": "MGL.NS", "petronet": "PETRONET.NS",
    "airtel": "BHARTIARTL.NS", "bharti": "BHARTIARTL.NS",
    "nestle": "NESTLEIND.NS", "kotak": "KOTAKBANK.NS",
    "hdfc bank": "HDFCBANK.NS", "hdfc life": "HDFCLIFE.NS",
    "icici bank": "ICICIBANK.NS", "axis bank": "AXISBANK.NS",
    "indusind": "INDUSINDBK.NS", "yes bank": "YESBANK.NS",
    "bandhan": "BANDHANBNK.NS", "federal bank": "FEDERALBNK.NS",
    "au bank": "AUBANK.NS", "idfc": "IDFCFIRSTB.NS",
    "idfc first": "IDFCFIRSTB.NS", "tata motors": "TATAMOTORS.NS",
    "tata steel": "TATASTEEL.NS", "tata power": "TATAPOWER.NS",
    "tata elxsi": "TATAELXSI.NS", "tata comm": "TATACOMM.NS",
    "tata consumer": "TATACONSUM.NS", "sun pharma": "SUNPHARMA.NS",
    "mrf": "MRF.NS", "bata": "BATAINDIA.NS", "asian paints": "ASIANPAINT.NS",
    "berger": "BERGEPAINT.NS", "nerolac": "KANSAINER.NS",
    "indigo": "INDIGO.NS", "indigo paints": "INDIGOPNTS.NS",
    "adani enterprises": "ADANIENT.NS", "adani ports": "ADANIPORTS.NS",
    "adani green": "ADANIGREEN.NS", "adani power": "ADANIPOWER.NS",
    "lumax": "LUMAXTECH.NS", "avanti": "AVANTIFEED.NS",
    "bluejet": "BLUEJET.NS", "blue jet": "BLUEJET.NS",
    "vimta": "VIMTALABS.NS", "gallantt": "GALLANTT.NS",
    "sarda": "SARDAEN.NS", "shaily": "SHAILY.NS",
    "polycab": "POLYCAB.NS", "kei": "KEI.NS",
    "crompton": "CROMPTON.NS", "blue star": "BLUESTARCO.NS",
    "abb": "ABB.NS", "siemens": "SIEMENS.NS",
    "honeywell": "HONAUT.NS", "hal": "HAL.NS",
    "bel": "BEL.NS", "bhel": "BHEL.NS",
    "irctc": "IRCTC.NS", "irfc": "IRFC.NS", "rvnl": "RVNL.NS",
    "zomato": "ZOMATO.NS", "paytm": "PAYTM.NS", "nykaa": "NYKAA.NS",
    "policy bazaar": "POLICYBZR.NS", "cdsl": "CDSL.NS",
    "bse": "BSE.NS", "mcx": "MCX.NS", "crisil": "CRISIL.NS",
    "hdfc amc": "HDFCAMC.NS", "pi industries": "PIIND.NS",
    "deepak nitrite": "DEEPAKNITR.NS", "vinati organics": "VINATIORGA.NS",
    "aarti": "AARTIIND.NS", "thermax": "THERMAX.NS",
    "cummins": "CUMMINSIND.NS", "schaeffler": "SCHAEFFLER.NS",
    "suzlon": "SUZLON.NS", "inox wind": "INOXWIND.NS",
    "ultratech": "ULTRACEMCO.NS", "shree cement": "SHREECEM.NS",
    "ambuja": "AMBUJACEM.NS", "dalmia": "DALMIA.NS",
    "jubilant foodworks": "JUBLFOOD.NS", "muthoot": "MUTHOOTFIN.NS",
    "chola": "CHOLAFIN.NS", "shriram": "SHRIRAMFIN.NS",
    "manappuram": "MANAPPURAM.NS", "divis": "DIVISLAB.NS",
    "lupin": "LUPIN.NS", "cipla": "CIPLA.NS", "biocon": "BIOCON.NS",
    "aurobindo": "AUROPHARMA.NS", "torrent pharma": "TORNTPHARM.NS",
    "ipca": "IPCALAB.NS", "alkem": "ALKEM.NS", "zydus": "ZYDUSLIFE.NS",
    "natco": "NATCOPHARMA.NS", "granules": "GRANULES.NS",
    "syngene": "SYNGENE.NS", "ajanta pharma": "AJANTPHARM.NS",
    "dr lal": "LALPATHLAB.NS", "metropolis": "METROPOLIS.NS",
    "max health": "MAXHEALTH.NS", "fortis": "FORTIS.NS",
    "apollo hosp": "APOLLOHOSP.NS", "coal india": "COALINDIA.NS",
    "hindalco": "HINDALCO.NS", "nmdc": "NMDC.NS", "sail": "SAIL.NS",
    "jspl": "JSPL.NS", "apl apollo": "APLAPOLLO.NS",
    "bharat forge": "BHARAT.NS", "sona blw": "SONA.NS",
    "endurance": "ENDURANCE.NS", "exide": "EXIDEIND.NS",
    "amara raja": "AMARAJABAT.NS", "ceat": "CEATLTD.NS",
    "apollo tyres": "APOLLOTYRE.NS", "balkrishna": "BALKRISIND.NS",
    "marico": "MARICO.NS", "dabur": "DABUR.NS",
    "britannia": "BRITANNIA.NS", "colgate": "COLPAL.NS",
    "raymond": "RAYMOND.NS", "trident": "TRIDENT.NS",
    "vardhman": "VARDHMAN.NS", "manyavar": "MANYAVAR.NS",
    "page ind": "PAGEIND.NS", "havells": "HAVELLS.NS",
    "voltas": "VOLTAS.NS", "whirlpool": "WHIRLPOOL.NS",
    "dlf": "DLF.NS", "godrej prop": "GODREJPROP.NS",
    "oberoi realty": "OBEROIRLTY.NS", "prestige": "PRESTIGE.NS",
    "sobha": "SOBHA.NS", "brigade": "BRIGADE.NS", "phoenix": "PHOENIXLTD.NS",
    "blue dart": "BLUEDART.NS", "delhivery": "DELHIVERY.NS",
    "kalyan jewellers": "KALYNA.NS", "zee": "ZEEL.NS",
    "pvr": "PVRINOX.NS", "sun tv": "SUNTV.NS",
    "varun beverages": "VARUNBEV.NS", "radico": "RADICO.NS",
    "concor": "CONCOR.NS", "ircon": "IRCON.NS", "rites": "RITES.NS",
    "coromandel": "COROMANDEL.NS", "persistent": "PERSISTENT.NS",
    "mphasis": "MPHASIS.NS", "coforge": "COFORGE.NS",
    "kpit": "KPITTECH.NS", "birlasoft": "BIRLASOFT.NS",
    "cyient": "CYIENT.NS", "tanla": "TANLA.NS", "intellect": "INTELLECT.NS",
    "route mobile": "ROUTE.NS", "justdial": "JUSTDIAL.NS",
    "indus towers": "INDUSTOWER.NS", "motilal": "MOTILALOFS.NS",
}
for k, v in _ALIASES.items():
    _name_map.setdefault(k.lower(), v)


# ── Autocomplete display list ────────────────────────────────────────
# Format: "Company Name (NSE)  ->  SYMBOL" or "Company Name (BSE)  ->  123456"
_seen_ac: set = set()
DISPLAY_LIST: list = []
# NSE first (preferred), then BSE-only
for exch_order in ("NSE", "BSE"):
    sub = _df[_df["exchange"] == exch_order].sort_values("company")
    for _, row in sub.iterrows():
        sym  = str(row["symbol"]).strip()
        name = str(row["company"]).strip().title()
        exch = str(row["exchange"])
        key  = f"{sym}_{exch}"
        if key not in _seen_ac:
            _seen_ac.add(key)
            DISPLAY_LIST.append(f"{name} ({exch})  ->  {sym}")


# ── Resolve function ─────────────────────────────────────────────────
def resolve_ticker(query: str) -> str | None:
    """
    Resolve a user search string to a yfinance ticker.
    Returns e.g. "RELIANCE.NS" or "500325.BO".
    """
    q = query.strip()
    if not q: return None

    # Already formatted
    if q.upper().endswith(".NS"): return q.upper()
    if q.upper().endswith(".BO"): return q.upper()

    lower = q.lower()

    # Exact name / alias match
    if lower in _name_map: return _name_map[lower]

    # Exact symbol match (NSE preferred)
    if lower in _sym_map: return _sym_map[lower]

    # Partial match — score by overlap length
    best, score = None, 0
    for name, ticker in _name_map.items():
        if lower in name or name in lower:
            s = len(set(lower.split()) & set(name.split()))
            if s > score:
                score, best = s, ticker
    if best: return best

    # Try partial symbol match
    for sym, ticker in _sym_map.items():
        if lower in sym:
            return ticker

    # Last resort: assume NSE raw symbol
    return q.upper() + ".NS"


def get_all_tickers() -> list[str]:
    """Return complete NSE+BSE ticker list (re-loads from disk to pick up updates)."""
    from universe_builder import load_universe
    df = load_universe()
    return df["ticker_ns"].tolist()
