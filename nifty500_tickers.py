"""
nifty500_tickers.py  —  MFM v4
Loads the full NSE + BSE equity universe from universe.parquet and exposes:

  ALL_TICKERS      list[str]   all yfinance tickers (.NS and .BO)
  NSE_TICKERS      list[str]   only .NS tickers (for RS ranking if needed)
  DISPLAY_ENTRIES  list[str]   autocomplete strings: "Company Name → SYMBOL (NSE)"
  DISPLAY_TO_TICKER dict       display string → ticker_yf
  NAME_TO_TICKER   dict        lowercase company name → preferred ticker_yf
  resolve_ticker(q) → str      parse user query to ticker_yf

Backward compat:
  NIFTY500_TICKERS = ALL_TICKERS  (used by data_fetcher RS cache)
"""

import os
import pandas as pd
from universe_builder import build_universe, load_universe, UNIVERSE_FILE


# ─────────────────────────────────────────────────────────────────────
#  Load universe on import
# ─────────────────────────────────────────────────────────────────────

def _load() -> pd.DataFrame:
    if os.path.exists(UNIVERSE_FILE):
        try:
            return pd.read_parquet(UNIVERSE_FILE)
        except Exception:
            pass
    try:
        return build_universe()
    except Exception:
        # Absolute minimum fallback so the app doesn't crash
        return pd.DataFrame({
            "ticker_yf":    ["RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS"],
            "display_name": ["Reliance Industries (NSE)","Tata Consultancy Services (NSE)",
                             "HDFC Bank (NSE)","Infosys (NSE)","ICICI Bank (NSE)"],
            "company_name": ["Reliance Industries","Tata Consultancy Services",
                             "HDFC Bank","Infosys","ICICI Bank"],
            "exchange":     ["NSE","NSE","NSE","NSE","NSE"],
            "symbol":       ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK"],
            "isin":         ["","","","",""],
            "industry":     ["Energy","IT","Banking","IT","Banking"],
        })

_UNIVERSE_DF: pd.DataFrame = _load()


# ─────────────────────────────────────────────────────────────────────
#  Core exports
# ─────────────────────────────────────────────────────────────────────

ALL_TICKERS: list[str] = _UNIVERSE_DF["ticker_yf"].tolist()
NSE_TICKERS: list[str] = _UNIVERSE_DF.loc[_UNIVERSE_DF["exchange"] == "NSE", "ticker_yf"].tolist()
BSE_TICKERS: list[str] = _UNIVERSE_DF.loc[_UNIVERSE_DF["exchange"] == "BSE", "ticker_yf"].tolist()

# Backward compat alias (RS cache uses this)
NIFTY500_TICKERS = ALL_TICKERS

# ─────────────────────────────────────────────────────────────────────
#  DISPLAY_ENTRIES  — for autocomplete
#  Format: "Company Name → SYMBOL (NSE)"  or  "Company Name → 500325 (BSE)"
# ─────────────────────────────────────────────────────────────────────

def _build_display_entries(df: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    """
    Build display entries list and reverse lookup dict.
    Sort: NSE entries before BSE entries, then alphabetically within each.
    """
    entries = []
    d2t = {}

    nse_part = df[df["exchange"] == "NSE"].sort_values("company_name")
    bse_part = df[df["exchange"] == "BSE"].sort_values("company_name")

    for _, row in pd.concat([nse_part, bse_part], ignore_index=True).iterrows():
        display = f"{row['company_name']}  →  {row['symbol']} ({row['exchange']})"
        entries.append(display)
        d2t[display] = row["ticker_yf"]

    return entries, d2t

DISPLAY_ENTRIES, DISPLAY_TO_TICKER = _build_display_entries(_UNIVERSE_DF)


# ─────────────────────────────────────────────────────────────────────
#  NAME_TO_TICKER  — fuzzy name lookup (prefers NSE over BSE)
# ─────────────────────────────────────────────────────────────────────

def _build_name_to_ticker(df: pd.DataFrame) -> dict[str, str]:
    """
    Maps lower-case company name → preferred ticker_yf.
    Prefers NSE ticker when both exchanges list the same company name.
    Also adds common aliases and short forms.
    """
    n2t: dict[str, str] = {}

    # BSE first (lower priority), then NSE overwrites
    for _, row in df[df["exchange"] == "BSE"].iterrows():
        name = row["company_name"].lower().strip()
        n2t[name] = row["ticker_yf"]
        # Also index by symbol (the numeric scrip code)
        n2t[row["symbol"].lower()] = row["ticker_yf"]

    for _, row in df[df["exchange"] == "NSE"].iterrows():
        name = row["company_name"].lower().strip()
        n2t[name] = row["ticker_yf"]
        # Also index by NSE symbol
        n2t[row["symbol"].lower()] = row["ticker_yf"]

    # Manual aliases covering common short forms / brand names
    _ALIASES = {
        # Big caps
        "reliance": "RELIANCE.NS", "ril": "RELIANCE.NS",
        "tcs": "TCS.NS", "tata consultancy": "TCS.NS",
        "hdfc bank": "HDFCBANK.NS", "hdfcbank": "HDFCBANK.NS",
        "infosys": "INFY.NS", "infy": "INFY.NS",
        "airtel": "BHARTIARTL.NS", "bharti airtel": "BHARTIARTL.NS",
        "icici bank": "ICICIBANK.NS", "icicibank": "ICICIBANK.NS",
        "sbi": "SBIN.NS", "state bank": "SBIN.NS",
        "hul": "HINDUNILVR.NS", "hindustan unilever": "HINDUNILVR.NS",
        "itc": "ITC.NS",
        "kotak": "KOTAKBANK.NS", "kotak bank": "KOTAKBANK.NS",
        "l&t": "LT.NS", "larsen": "LT.NS", "larsen toubro": "LT.NS",
        "axis bank": "AXISBANK.NS", "axisbank": "AXISBANK.NS",
        "asian paints": "ASIANPAINT.NS", "asianpaint": "ASIANPAINT.NS",
        "maruti": "MARUTI.NS", "maruti suzuki": "MARUTI.NS",
        "bajaj finance": "BAJFINANCE.NS", "bajfinance": "BAJFINANCE.NS",
        "ntpc": "NTPC.NS",
        "ongc": "ONGC.NS",
        "sun pharma": "SUNPHARMA.NS", "sunpharma": "SUNPHARMA.NS",
        "titan": "TITAN.NS",
        "power grid": "POWERGRID.NS",
        "ultratech": "ULTRACEMCO.NS", "ultratech cement": "ULTRACEMCO.NS",
        "wipro": "WIPRO.NS",
        "hcl": "HCLTECH.NS", "hcl tech": "HCLTECH.NS", "hcltech": "HCLTECH.NS",
        "nestle": "NESTLEIND.NS", "nestleind": "NESTLEIND.NS",
        "adani": "ADANIENT.NS",
        "bajaj finserv": "BAJAJFINSV.NS", "bajajfinsv": "BAJAJFINSV.NS",
        "tech mahindra": "TECHM.NS", "techm": "TECHM.NS",
        "grasim": "GRASIM.NS",
        "coal india": "COALINDIA.NS",
        "indusind": "INDUSINDBK.NS", "indusind bank": "INDUSINDBK.NS",
        "tata motors": "TATAMOTORS.NS", "tatamotors": "TATAMOTORS.NS",
        "tata steel": "TATASTEEL.NS", "tatasteel": "TATASTEEL.NS",
        "m&m": "M&M.NS", "mahindra": "M&M.NS",
        "jsw steel": "JSWSTEEL.NS", "jswsteel": "JSWSTEEL.NS",
        "cipla": "CIPLA.NS",
        "apollo hospitals": "APOLLOHOSP.NS", "apollo hospital": "APOLLOHOSP.NS",
        "eicher": "EICHERMOT.NS", "eicher motors": "EICHERMOT.NS",
        "ltimindtree": "LTIM.NS", "ltim": "LTIM.NS",
        "hindalco": "HINDALCO.NS",
        "dr reddy": "DRREDDY.NS", "dr reddys": "DRREDDY.NS", "drreddy": "DRREDDY.NS",
        "britannia": "BRITANNIA.NS",
        "sbi life": "SBILIFE.NS",
        "hero": "HEROMOTOCO.NS", "hero motocorp": "HEROMOTOCO.NS",
        "hdfc life": "HDFCLIFE.NS",
        "divi": "DIVISLAB.NS", "divis": "DIVISLAB.NS",
        "bajaj auto": "BAJAJ-AUTO.NS",
        "upl": "UPL.NS",
        # Mid caps
        "zomato": "ZOMATO.NS",
        "paytm": "PAYTM.NS",
        "nykaa": "NYKAA.NS",
        "policybazaar": "POLICYBZR.NS", "policy bazaar": "POLICYBZR.NS",
        "irctc": "IRCTC.NS",
        "hal": "HAL.NS", "hindustan aeronautics": "HAL.NS",
        "bel": "BEL.NS", "bharat electronics": "BEL.NS",
        "rvnl": "RVNL.NS",
        "irfc": "IRFC.NS",
        "dmart": "DMART.NS", "d-mart": "DMART.NS", "avenue supermarts": "DMART.NS",
        "naukri": "NAUKRI.NS", "info edge": "NAUKRI.NS",
        "indigo": "INDIGO.NS", "interglobe": "INDIGO.NS",
        "dlf": "DLF.NS",
        "trent": "TRENT.NS",
        "page industries": "PAGEIND.NS",
        "pidilite": "PIDILITIND.NS",
        "marico": "MARICO.NS",
        "dabur": "DABUR.NS",
        "berger": "BERGEPAINT.NS", "berger paints": "BERGEPAINT.NS",
        "muthoot": "MUTHOOTFIN.NS", "muthoot finance": "MUTHOOTFIN.NS",
        "torrent pharma": "TORNTPHARM.NS",
        "lupin": "LUPIN.NS",
        "biocon": "BIOCON.NS",
        "rec": "RECLTD.NS",
        "pfc": "PFC.NS",
        "ofss": "OFSS.NS", "oracle financial": "OFSS.NS",
        "aurobindo": "AUROPHARMA.NS",
        "colgate": "COLPAL.NS", "colpal": "COLPAL.NS",
        "shree cement": "SHREECEM.NS",
        "acc": "ACC.NS",
        "tvsmotor": "TVSMOTOR.NS", "tvs motor": "TVSMOTOR.NS",
        "sbi cards": "SBICARD.NS",
        "bandhan": "BANDHANBNK.NS",
        "federal bank": "FEDERALBNK.NS",
        "mphasis": "MPHASIS.NS",
        "coforge": "COFORGE.NS",
        "ltts": "LTTS.NS",
        "persistent": "PERSISTENT.NS",
        "vedanta": "VEDL.NS", "vedl": "VEDL.NS",
        "siemens": "SIEMENS.NS",
        "havells": "HAVELLS.NS",
        "bosch": "BOSCHLTD.NS",
        "srf": "SRF.NS",
        "tata power": "TATAPOWER.NS",
        "sail": "SAIL.NS",
        "pnb": "PNB.NS",
        "canara": "CANBK.NS", "canara bank": "CANBK.NS",
        "chola": "CHOLAFIN.NS", "cholamandalam": "CHOLAFIN.NS",
        "godrej properties": "GODREJPROP.NS",
        "godrej cp": "GODREJCP.NS",
        "lic housing": "LICHSGFIN.NS",
        "polycab": "POLYCAB.NS",
        "kei": "KEI.NS",
        "astral": "ASTRAL.NS",
        "supremeind": "SUPREMEIND.NS",
        "dixon": "DIXON.NS",
        "amber": "AMBER.NS",
        "kaynes": "KAYNES.NS",
        "kpit": "KPITTECH.NS", "kpit tech": "KPITTECH.NS",
        "tata elxsi": "TATAELXSI.NS",
        "taj hotels": "INDHOTEL.NS", "indian hotels": "INDHOTEL.NS", "taj": "INDHOTEL.NS",
        "oberoi hotels": "EIHOTEL.NS",
        "lemon tree": "LEMON.NS",
        "jspl": "JSPL.NS", "jindal steel power": "JSPL.NS",
        "nmdc": "NMDC.NS",
        "apl apollo": "APLAPOLLO.NS",
        "pi industries": "PIIND.NS",
        "deepak nitrite": "DEEPAKNITR.NS",
        "vinati": "VINATIORGA.NS",
        "aarti industries": "AARTIIND.NS",
        "tata chemicals": "TATACHEM.NS",
        "clean science": "CLEAN.NS",
        "fine organic": "FINEORG.NS",
        "gail": "GAIL.NS",
        "igl": "IGL.NS", "indraprastha gas": "IGL.NS",
        "mgl": "MGL.NS", "mahanagar gas": "MGL.NS",
        "petronet": "PETRONET.NS",
        "gujarat gas": "GUJGASLTD.NS",
        "suzlon": "SUZLON.NS",
        "kec": "KEC.NS",
        "oberoi realty": "OBEROIRLTY.NS",
        "phoenix mills": "PHOENIXLTD.NS",
        "prestige": "PRESTIGE.NS",
        "sobha": "SOBHA.NS",
        "brigade": "BRIGADE.NS",
        "dominos": "JUBLFOOD.NS", "jubilant food": "JUBLFOOD.NS",
        "mcdonalds": "WESTLIFE.NS",
        "metro brands": "METROBRAND.NS",
        "manyavar": "MANYAVAR.NS",
        "honeywell": "HONAUT.NS",
        "whirlpool": "WHIRLPOOL.NS",
        "bluedart": "BLUEDART.NS", "blue dart": "BLUEDART.NS",
        "delhivery": "DELHIVERY.NS",
        "varun beverages": "VARUNBEV.NS",
        "radico": "RADICO.NS",
        "jsw energy": "JSWENERGY.NS",
        "adani green": "ADANIGREEN.NS",
        "adani power": "ADANIPOWER.NS",
        "kalyan jewellers": "KALYNA.NS",
        "senco": "SENCO.NS",
        "beml": "BEML.NS",
        "cochin shipyard": "COCHINSHIP.NS",
        "mazagon": "MAZDOCK.NS", "mazagon dock": "MAZDOCK.NS",
        "garden reach": "GRSE.NS",
        "solar industries": "SOLARINDS.NS",
        "data patterns": "DATAPATTNS.NS",
        "mtar": "MTAR.NS",
        "360 one": "360ONE.NS", "360one": "360ONE.NS",
        "nuvama": "NUVAMA.NS",
        "indigo paints": "INDIGOPNTS.NS",
        "kansai": "KANSAINER.NS", "nerolac": "KANSAINER.NS",
        "sun tv": "SUNTV.NS",
        "pvr": "PVRINOX.NS", "inox": "PVRINOX.NS",
        "balrampur": "BALRAMCHIN.NS",
        "coromandel": "COROMANDEL.NS",
        "syngene": "SYNGENE.NS",
        "ajanta pharma": "AJANTPHARM.NS",
        "gland pharma": "GLAND.NS",
        "caplin point": "CAPLIPOINT.NS",
        "strides": "STRIDES.NS",
        "neuland": "NEULANDLAB.NS",
        "jb chemicals": "JBCHEPHARM.NS",
        "mankind": "MANKIND.NS",
        "zydus": "ZYDUSLIFE.NS",
        "natco": "NATCOPHARMA.NS",
        "granules": "GRANULES.NS",
        "endurance": "ENDURANCE.NS",
        "sona": "SONA.NS",
        "gabriel": "GABRIEL.NS",
        "ceat": "CEATLTD.NS",
        "mrf": "MRF.NS",
        "apollo tyres": "APOLLOTYRE.NS",
        "balkrishna": "BALKRISIND.NS", "bkt": "BALKRISIND.NS",
        "tata comm": "TATACOMM.NS",
        "hfcl": "HFCL.NS",
        "minda": "MINDA.NS",
        "craftsman": "CRAFTSMAN.NS",
        "suprajit": "SUPRAJIT.NS",
        "pricol": "PRICOL.NS",
        "rolex rings": "ROLEX.NS",
        "exide": "EXIDEIND.NS",
        "amara raja": "AMARAJABAT.NS",
        "avanti feeds": "AVANTIFEED.NS",
        "lumax tech": "LUMAXTECH.NS", "lumax technologies": "LUMAXTECH.NS",
        "vimta": "VIMTALABS.NS",
        "gallantt": "GALLANTT.NS",
        "sarda": "SARDAEN.NS",
        "shaily": "SHAILY.NS",
        "blue jet": "BLUEJET.NS",
        "wonderla": "WONDERLA.NS",
        "ircon": "IRCON.NS",
        "rites": "RITES.NS",
        "wabag": "WABAG.NS",
        "lemon tree hotels": "LEMON.NS",
        "uti amc": "UTI.NS",
        "hdfc amc": "HDFCAMC.NS",
        "nippon amc": "NIPPONLIFE.NS",
        "birlasoft": "BIRLASOFT.NS",
        "cyient": "CYIENT.NS",
        "tanla": "TANLA.NS",
        "route mobile": "ROUTE.NS",
        "crisil": "CRISIL.NS",
        "iifl": "IIFL.NS",
        "poonawalla": "POONAWALLA.NS",
        "au bank": "AUBANK.NS",
        "rbl bank": "RBLBANK.NS",
        "idfc first": "IDFCFIRSTB.NS", "idfcfirst": "IDFCFIRSTB.NS",
        "yes bank": "YESBANK.NS",
        "idbi bank": "IDBI.NS",
        "lalpathlab": "LALPATHLAB.NS", "dr lal": "LALPATHLAB.NS",
        "metropolis": "METROPOLIS.NS",
        "max healthcare": "MAXHEALTH.NS",
        "fortis": "FORTIS.NS",
        "narayana": "NARAYANA.NS",
        "rainbow": "RAINBOW.NS",
        "ipca": "IPCALAB.NS",
        "alkem": "ALKEM.NS",
        "glenmark": "GLENMARK.NS",
        "torrent power": "TORNTPOWER.NS",
        "cesc": "CESC.NS",
        "eclerx": "ECLERX.NS",
        "mastek": "MASTEK.NS",
        "zensar": "ZENSAR.NS",
        "intellect": "INTELLECT.NS",
        "infibeam": "INFIBEAM.NS",
        "chalet hotels": "CHALET.NS",
        "shriram finance": "SHRIRAMFIN.NS",
        "manappuram": "MANAPPURAM.NS",
        "creditaccess": "CREDITACC.NS",
        "spandana": "SPANDANA.NS",
        "ratnamani": "RATNAMANI.NS",
        "welspun": "WELSPUNLIV.NS",
        "raymond": "RAYMOND.NS",
        "trident": "TRIDENT.NS",
        "vardhman": "VARDHMAN.NS",
        "kpr mill": "KPR.NS",
        "v-guard": "VGUARD.NS",
        "bajaj electricals": "BAJAJELEC.NS",
        "crompton": "CROMPTON.NS",
        "blue star": "BLUESTARCO.NS",
        "abb": "ABB.NS",
        "apar": "APARINDS.NS",
        "schaeffler": "SCHAEFFLER.NS",
        "cummins": "CUMMINSIND.NS",
        "thermax": "THERMAX.NS",
        "praj": "PRAJ.NS",
        "elgi": "ELGI.NS",
        "grindwell": "GRINDWELL.NS",
        "century ply": "CENTURYPLY.NS",
        "greenpanel": "GREENPANEL.NS",
        "finolex": "FINOLEX.NS",
        "jk cement": "JKCEMENT.NS",
        "ramco cement": "RAMCOCEM.NS",
        "dalmia": "DALMIA.NS",
        "birla corporation": "BIRLACORPN.NS",
        "pi ind": "PIIND.NS",
        "sumitomo chem": "SUMICHEM.NS",
        "gnfc": "GNFC.NS",
        "nocil": "NOCIL.NS",
        "laxmi organic": "LAXMIPATHI.NS",
        "anupam rasayan": "ANUPAM.NS",
        "epigral": "EPIGRAL.NS",
        "aegis logistics": "AEGISCHEM.NS",
        "linde": "LINDEINDIA.NS",
        "inox wind": "INOXWIND.NS",
        "borosil": "BOROSIL.NS",
        "kalpataru": "KALPATPOWR.NS",
        "pnc infratech": "PNC.NS",
        "knr": "KNRCON.NS",
        "hg infra": "HGINFRA.NS",
        "mahindra lifespace": "MAHLIFE.NS",
        "sunteck": "SUNTECK.NS",
        "just dial": "JUSTDIAL.NS",
        "indigo paints": "INDIGOPNTS.NS",
        "gillette": "GILLETTE.NS",
        "3m india": "3MINDIA.NS",
        "ccl products": "CCL.NS",
        "tata coffee": "TATACOFFEE.NS",
        "globus spirits": "GLOBUSSP.NS",
        "gmm pfaudler": "GMMPFAUDLR.NS",
        "ideaforge": "IDEAFORGE.NS",
        "paras defence": "PARAS.NS",
        "shoppers stop": "SHOPERSTOP.NS",
        "vmart": "VMART.NS",
        "bataindia": "BATAINDIA.NS", "bata": "BATAINDIA.NS",
        "relaxo": "RELAXO.NS",
        "rajesh exports": "RAJESHEXPO.NS",
        "thangamayil": "THANGAMAYL.NS",
        "sjs enterprises": "SJS.NS",
        "sundram fasteners": "SUNDRAMFAST.NS",
        "skf": "SKFINDIA.NS",
        "saregama": "SAREGAMA.NS",
        "tips music": "TIPS.NS",
        "gsfc": "GSFC.NS",
        "zuari": "ZUARI.NS",
        "renuka sugars": "RENUKA.NS",
        "triveni": "TRIVENI.NS",
        "dhampur": "DHAMPURSUG.NS",
        "sapphire foods": "SAPPHIRE.NS",
        "barbeque nation": "BARBEQUE.NS",
        "tatva chintan": "TATVA.NS",
        "hathway": "HATHWAY.NS",
        "zee": "ZEEL.NS", "zee entertainment": "ZEEL.NS",
        "bank of baroda": "BANKBARODA.NS",
        "motherson": "MOTHERSON.NS",
        "allcargo": "ALLCARGO.NS",
        "tci": "TCI.NS",
        "vrl": "VRL.NS",
        "gati": "GATI.NS",
        "angel one": "ANGELONE.NS",
        "motilal": "MOTILALOFS.NS",
        "icicigi": "ICICIGI.NS", "icici lombard": "ICICIGI.NS",
        "star health": "STAR.NS",
        "new india assurance": "NIACL.NS",
        "konkani": "KFINTECH.NS",
        "max financial": "MFSL.NS",
        "equitas": "EQUITASBNK.NS",
        "ujjivan": "UJJIVANSFB.NS",
        "suryoday": "SURYODAY.NS",
        "city union bank": "CITYUNIONBK.NS",
        "karur vysya": "KARURVYSYA.NS",
        "indian bank": "INDIANB.NS",
        "dcb bank": "DCBBANK.NS",
        "concor": "CONCOR.NS",
        "abslamc": "ABSLAMC.NS",
        "uti": "UTI.NS",
    }

    for alias, ticker in _ALIASES.items():
        if alias not in n2t:
            n2t[alias] = ticker

    return n2t


NAME_TO_TICKER: dict[str, str] = _build_name_to_ticker(_UNIVERSE_DF)


# ─────────────────────────────────────────────────────────────────────
#  resolve_ticker — maps any user query to a yfinance ticker
# ─────────────────────────────────────────────────────────────────────

def resolve_ticker(query: str) -> str:
    """
    Resolve a user-typed query to a yfinance ticker string.
    Priority:
      1. Exact match in NAME_TO_TICKER (covers aliases + company names)
      2. Starts-with match in DISPLAY_ENTRIES → parse ticker from display
      3. If numeric → assume BSE scrip code → return code.BO
      4. If looks like NSE symbol → return QUERY.NS
      5. Fallback: RELIANCE.NS
    """
    if not query:
        return "RELIANCE.NS"

    q = query.strip()

    # Strip exchange suffix if user typed "RELIANCE (NSE)" or "500325 (BSE)"
    for suffix in [" (NSE)", " (BSE)", ".NS", ".BO"]:
        if q.upper().endswith(suffix.upper()):
            q = q[: -len(suffix)].strip()
            break

    ql = q.lower()

    # 1. Exact match in NAME_TO_TICKER
    if ql in NAME_TO_TICKER:
        return NAME_TO_TICKER[ql]

    # 2. Substring match in display entries
    matches = [e for e in DISPLAY_ENTRIES if ql in e.lower()]
    if matches:
        # Prefer NSE if multiple matches
        nse_matches = [e for e in matches if "(NSE)" in e]
        best = nse_matches[0] if nse_matches else matches[0]
        return DISPLAY_TO_TICKER[best]

    # 3. Numeric → BSE scrip code
    if q.isdigit():
        return q + ".BO"

    # 4. Alphanumeric uppercase-ish → treat as NSE symbol
    return q.upper() + ".NS"


# ─────────────────────────────────────────────────────────────────────
#  Quick self-test
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Universe loaded: {len(ALL_TICKERS)} tickers ({len(NSE_TICKERS)} NSE + {len(BSE_TICKERS)} BSE)")
    print(f"Autocomplete entries: {len(DISPLAY_ENTRIES)}")
    print(f"Name→Ticker lookup: {len(NAME_TO_TICKER)} entries")
    for q in ["Asian Paints", "tcs", "zomato", "500325", "LUMAXTECH", "baj", "hal"]:
        print(f"  resolve_ticker({q!r}) → {resolve_ticker(q)}")
