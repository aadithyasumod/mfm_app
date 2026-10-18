"""
universe_builder.py  —  MFM v4
Builds a complete NSE + BSE equity universe and saves to universe.parquet.

Sources (tried in order for each exchange):
  NSE  →  archives.nseindia.com/content/equities/EQUITY_L.csv   (~2 000 stocks)
  BSE  →  api.bseindia.com ListofScripData JSON endpoint        (~5 000 stocks)
  Fallback → expanded hardcoded list for both exchanges

Output schema (universe.parquet):
  ticker_yf    str   yfinance ticker  e.g. "RELIANCE.NS" or "500325.BO"
  display_name str   "Reliance Industries (NSE)"
  company_name str   "Reliance Industries"
  exchange     str   "NSE" or "BSE"
  symbol       str   NSE symbol or BSE scrip code string
  isin         str   ISIN code (may be empty for some BSE stocks)
  industry     str   sector / industry string

Auto-builds on first run, refreshes after UNIVERSE_TTL_DAYS (30 days).
"""

import os, io, json, time, requests, warnings
import pandas as pd
from datetime import datetime

warnings.filterwarnings("ignore")

UNIVERSE_FILE   = "universe.parquet"
UNIVERSE_TTL_DAYS = 30

# ── NSE source ──────────────────────────────────────────────────────
NSE_EQUITY_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

# ── BSE source ──────────────────────────────────────────────────────
BSE_API_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
    "?Group=&Scripcode=&industry=&segment=Equity&status=Active"
)

_HEADERS_NSE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/",
}
_HEADERS_BSE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}


# ═══════════════════════════════════════════════════════════════════
#  NSE FETCH
# ═══════════════════════════════════════════════════════════════════

def _fetch_nse() -> pd.DataFrame | None:
    """Fetch NSE equity master CSV. Filters to EQ series (main equity board)."""
    try:
        r = requests.get(NSE_EQUITY_URL, headers=_HEADERS_NSE, timeout=25)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]

        # Normalise column names
        col_map = {}
        for c in df.columns:
            lc = c.lower()
            if "symbol" in lc:             col_map[c] = "SYMBOL"
            elif "name" in lc:             col_map[c] = "NAME"
            elif "series" in lc:           col_map[c] = "SERIES"
            elif "isin" in lc:             col_map[c] = "ISIN"
        df = df.rename(columns=col_map)

        if "SYMBOL" not in df.columns:
            print("  ✗ NSE CSV: SYMBOL column not found")
            return None

        # Keep main equity series
        if "SERIES" in df.columns:
            df = df[df["SERIES"].str.strip().isin(["EQ", "BE", "BL", "IL"])]

        df["SYMBOL"]   = df["SYMBOL"].str.strip()
        df["NAME"]     = df["NAME"].str.strip()   if "NAME" in df.columns else df["SYMBOL"]
        df["ISIN"]     = df["ISIN"].str.strip()   if "ISIN" in df.columns else ""

        result = pd.DataFrame({
            "ticker_yf":    df["SYMBOL"] + ".NS",
            "display_name": df["NAME"] + " (NSE)",
            "company_name": df["NAME"],
            "exchange":     "NSE",
            "symbol":       df["SYMBOL"],
            "isin":         df.get("ISIN", ""),
            "industry":     "",
        })
        result = result.dropna(subset=["ticker_yf"])
        result = result[result["symbol"].str.len() >= 1]
        print(f"  ✓ NSE: {len(result)} equities fetched from EQUITY_L.csv")
        return result

    except Exception as e:
        print(f"  ✗ NSE fetch failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
#  BSE FETCH
# ═══════════════════════════════════════════════════════════════════

def _fetch_bse() -> pd.DataFrame | None:
    """Fetch BSE equity scrip master from BSE API. Returns scrip-code based rows."""
    try:
        r = requests.get(BSE_API_URL, headers=_HEADERS_BSE, timeout=30)
        r.raise_for_status()
        data = r.json()

        if isinstance(data, dict):
            # Some BSE API responses wrap in a key
            for key in ["Table", "data", "scrips", "result"]:
                if key in data:
                    data = data[key]
                    break

        if not isinstance(data, list) or len(data) < 10:
            print(f"  ✗ BSE API: unexpected response format (got {type(data)})")
            return None

        rows = []
        for item in data:
            scrip_code  = str(item.get("Scripcode", item.get("SCRIPCODE", ""))).strip()
            name        = str(item.get("LONG_NAME", item.get("compname", item.get("Issuer_Name", "")))).strip().title()
            isin        = str(item.get("isin_code",  item.get("ISIN_CODE", ""))).strip()
            industry    = str(item.get("Industry_new", item.get("industry", ""))).strip().title()

            if not scrip_code or not scrip_code.isdigit():
                continue
            if not name or name.lower() in ("nan", "none", ""):
                continue

            rows.append({
                "ticker_yf":    scrip_code + ".BO",
                "display_name": name + " (BSE)",
                "company_name": name,
                "exchange":     "BSE",
                "symbol":       scrip_code,
                "isin":         isin,
                "industry":     industry,
            })

        if len(rows) < 50:
            print(f"  ✗ BSE API returned too few rows ({len(rows)})")
            return None

        result = pd.DataFrame(rows)
        print(f"  ✓ BSE: {len(result)} equities fetched from API")
        return result

    except Exception as e:
        print(f"  ✗ BSE fetch failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
#  HARDCODED FALLBACK  (~400 NSE + ~100 BSE-only)
# ═══════════════════════════════════════════════════════════════════

_NSE_FALLBACK = [
    # symbol, company_name, industry
    # ── NIFTY 50 ──────────────────────────────────────────────
    ("RELIANCE","Reliance Industries","Energy"),("TCS","Tata Consultancy Services","IT"),
    ("HDFCBANK","HDFC Bank","Banking"),("INFY","Infosys","IT"),
    ("BHARTIARTL","Bharti Airtel","Telecom"),("ICICIBANK","ICICI Bank","Banking"),
    ("SBIN","State Bank of India","Banking"),("HINDUNILVR","Hindustan Unilever","FMCG"),
    ("ITC","ITC","FMCG"),("KOTAKBANK","Kotak Mahindra Bank","Banking"),
    ("LT","Larsen & Toubro","Capital Goods"),("AXISBANK","Axis Bank","Banking"),
    ("ASIANPAINT","Asian Paints","Consumer"),("MARUTI","Maruti Suzuki","Auto"),
    ("BAJFINANCE","Bajaj Finance","NBFC"),("NTPC","NTPC","Power"),
    ("ONGC","ONGC","Oil & Gas"),("SUNPHARMA","Sun Pharmaceutical","Pharma"),
    ("TITAN","Titan Company","Consumer"),("POWERGRID","Power Grid Corporation","Power"),
    ("ULTRACEMCO","UltraTech Cement","Cement"),("WIPRO","Wipro","IT"),
    ("HCLTECH","HCL Technologies","IT"),("NESTLEIND","Nestle India","FMCG"),
    ("ADANIENT","Adani Enterprises","Conglomerate"),("ADANIPORTS","Adani Ports","Infrastructure"),
    ("BAJAJFINSV","Bajaj Finserv","Finance"),("TECHM","Tech Mahindra","IT"),
    ("GRASIM","Grasim Industries","Cement"),("COALINDIA","Coal India","Mining"),
    ("INDUSINDBK","IndusInd Bank","Banking"),("TATAMOTORS","Tata Motors","Auto"),
    ("TATASTEEL","Tata Steel","Metals"),("M&M","Mahindra & Mahindra","Auto"),
    ("JSWSTEEL","JSW Steel","Metals"),("TATACONSUM","Tata Consumer Products","FMCG"),
    ("BPCL","BPCL","Oil & Gas"),("CIPLA","Cipla","Pharma"),
    ("APOLLOHOSP","Apollo Hospitals","Healthcare"),("EICHERMOT","Eicher Motors","Auto"),
    ("LTIM","LTIMindtree","IT"),("HINDALCO","Hindalco Industries","Metals"),
    ("DRREDDY","Dr. Reddy's Laboratories","Pharma"),("BRITANNIA","Britannia Industries","FMCG"),
    ("SBILIFE","SBI Life Insurance","Insurance"),("HEROMOTOCO","Hero MotoCorp","Auto"),
    ("HDFCLIFE","HDFC Life Insurance","Insurance"),("DIVISLAB","Divi's Laboratories","Pharma"),
    ("BAJAJ-AUTO","Bajaj Auto","Auto"),("UPL","UPL","Agro Chemicals"),
    # ── NIFTY NEXT 50 ─────────────────────────────────────────
    ("VEDL","Vedanta","Metals"),("IOC","Indian Oil Corporation","Oil & Gas"),
    ("SIEMENS","Siemens India","Capital Goods"),("HAVELLS","Havells India","Electricals"),
    ("BOSCHLTD","Bosch","Auto Ancillaries"),("PIDILITIND","Pidilite Industries","Chemicals"),
    ("MARICO","Marico","FMCG"),("DABUR","Dabur India","FMCG"),
    ("BERGEPAINT","Berger Paints","Consumer"),("MUTHOOTFIN","Muthoot Finance","NBFC"),
    ("TORNTPHARM","Torrent Pharmaceuticals","Pharma"),("AMBUJACEM","Ambuja Cements","Cement"),
    ("GODREJCP","Godrej Consumer Products","FMCG"),("LUPIN","Lupin","Pharma"),
    ("BIOCON","Biocon","Pharma"),("MOTHERSON","Samvardhana Motherson","Auto Ancillaries"),
    ("DLF","DLF","Real Estate"),("SRF","SRF","Chemicals"),
    ("INDIGO","InterGlobe Aviation","Airlines"),("TATAPOWER","Tata Power","Power"),
    ("SAIL","Steel Authority of India","Metals"),("PNB","Punjab National Bank","Banking"),
    ("BANKBARODA","Bank of Baroda","Banking"),("CANBK","Canara Bank","Banking"),
    ("CHOLAFIN","Cholamandalam Investment","NBFC"),("PAGEIND","Page Industries","Consumer"),
    ("GODREJPROP","Godrej Properties","Real Estate"),("LICHSGFIN","LIC Housing Finance","NBFC"),
    ("RECLTD","REC","Finance"),("PFC","Power Finance Corporation","Finance"),
    ("NAUKRI","Info Edge","Internet"),("OFSS","Oracle Financial Services","IT"),
    ("AUROPHARMA","Aurobindo Pharma","Pharma"),("COLPAL","Colgate-Palmolive India","FMCG"),
    ("SHREECEM","Shree Cement","Cement"),("ACC","ACC","Cement"),
    ("TRENT","Trent","Retail"),("VOLTAS","Voltas","Consumer"),
    ("TVSMOTOR","TVS Motor Company","Auto"),("DMART","Avenue Supermarts","Retail"),
    ("SBICARD","SBI Cards","Finance"),("BANDHANBNK","Bandhan Bank","Banking"),
    ("FEDERALBNK","Federal Bank","Banking"),("MPHASIS","Mphasis","IT"),
    ("COFORGE","Coforge","IT"),("LTTS","L&T Technology Services","IT"),
    ("PERSISTENT","Persistent Systems","IT"),("INDUSTOWER","Indus Towers","Telecom"),
    # ── MIDCAP / SMALLCAP ─────────────────────────────────────
    ("ZOMATO","Zomato","Internet"),("PAYTM","One97 Communications","Fintech"),
    ("NYKAA","FSN E-Commerce","Retail"),("POLICYBZR","PB Fintech","Fintech"),
    ("IRCTC","IRCTC","Travel"),("CONCOR","Container Corporation","Logistics"),
    ("HAL","Hindustan Aeronautics","Defence"),("BEL","Bharat Electronics","Defence"),
    ("BHEL","BHEL","Capital Goods"),("NHPC","NHPC","Power"),
    ("SJVN","SJVN","Power"),("RVNL","Rail Vikas Nigam","Infrastructure"),
    ("IRFC","IRFC","Finance"),("NBCC","NBCC India","Infrastructure"),
    ("BSE","BSE Limited","Exchange"),("MCX","Multi Commodity Exchange","Exchange"),
    ("CDSL","CDSL","Finance"),("ANGELONE","Angel One","Broking"),
    ("MOTILALOFS","Motilal Oswal Financial","Broking"),("HDFCAMC","HDFC AMC","AMC"),
    ("NIPPONLIFE","Nippon India AMC","AMC"),("ICICIGI","ICICI Lombard GIC","Insurance"),
    ("STAR","Star Health Insurance","Insurance"),("KFINTECH","KFin Technologies","Fintech"),
    ("CRISIL","CRISIL","Finance"),("MFSL","Max Financial Services","Insurance"),
    ("AUBANK","AU Small Finance Bank","Banking"),("RBLBANK","RBL Bank","Banking"),
    ("IDFCFIRSTB","IDFC First Bank","Banking"),("YESBANK","Yes Bank","Banking"),
    ("IDBI","IDBI Bank","Banking"),("LALPATHLAB","Dr. Lal PathLabs","Healthcare"),
    ("METROPOLIS","Metropolis Healthcare","Healthcare"),("MAXHEALTH","Max Healthcare","Healthcare"),
    ("FORTIS","Fortis Healthcare","Healthcare"),("NARAYANA","Narayana Hrudayalaya","Healthcare"),
    ("IPCALAB","IPCA Laboratories","Pharma"),("ALKEM","Alkem Laboratories","Pharma"),
    ("GLENMARK","Glenmark Pharmaceuticals","Pharma"),("TORNTPOWER","Torrent Power","Power"),
    ("CESC","CESC","Power"),("TATAELXSI","Tata Elxsi","IT"),
    ("DIXON","Dixon Technologies","Electronics"),("AMBER","Amber Enterprises","Electronics"),
    ("KAYNES","Kaynes Technology","Electronics"),("ECLERX","eClerx Services","IT"),
    ("BIRLASOFT","Birlasoft","IT"),("KPITTECH","KPIT Technologies","IT"),
    ("CYIENT","Cyient","IT"),("TANLA","Tanla Platforms","IT"),
    ("INTELLECT","Intellect Design Arena","IT"),("ROUTE","Route Mobile","IT"),
    ("INDHOTEL","Indian Hotels","Hotels"),("EIHOTEL","EIH (Oberoi Hotels)","Hotels"),
    ("CHALET","Chalet Hotels","Hotels"),("LEMON","Lemon Tree Hotels","Hotels"),
    ("SUNDARMFIN","Sundaram Finance","NBFC"),("SHRIRAMFIN","Shriram Finance","NBFC"),
    ("MANAPPURAM","Manappuram Finance","NBFC"),("L&TFH","L&T Finance","NBFC"),
    ("CREDITACC","CreditAccess Grameen","MFI"),("APLAPOLLO","APL Apollo Tubes","Metals"),
    ("JSPL","Jindal Steel & Power","Metals"),("NMDC","NMDC","Mining"),
    ("RATNAMANI","Ratnamani Metals","Metals"),("WELSPUNLIV","Welspun Living","Textiles"),
    ("RAYMOND","Raymond","Textiles"),("TRIDENT","Trident","Textiles"),
    ("VARDHMAN","Vardhman Textiles","Textiles"),("KPR","KPR Mill","Textiles"),
    ("POLYCAB","Polycab India","Electricals"),("KEI","KEI Industries","Electricals"),
    ("FINCABLES","Finolex Cables","Electricals"),("VGUARD","V-Guard Industries","Electricals"),
    ("BAJAJELEC","Bajaj Electricals","Electricals"),("CROMPTON","Crompton Greaves Consumer","Electricals"),
    ("BLUESTARCO","Blue Star","Electricals"),("ABB","ABB India","Capital Goods"),
    ("APARINDS","APAR Industries","Electricals"),("SCHAEFFLER","Schaeffler India","Auto Ancillaries"),
    ("CUMMINSIND","Cummins India","Capital Goods"),("THERMAX","Thermax","Capital Goods"),
    ("PRAJ","Praj Industries","Capital Goods"),("SUPREMEIND","Supreme Industries","Plastics"),
    ("ASTRAL","Astral Poly Technik","Plastics"),("JKCEMENT","JK Cement","Cement"),
    ("RAMCOCEM","The Ramco Cements","Cement"),("DALMIA","Dalmia Bharat","Cement"),
    ("PIIND","PI Industries","Agro Chemicals"),("SUMICHEM","Sumitomo Chemical India","Agro Chemicals"),
    ("AARTIIND","Aarti Industries","Chemicals"),("VINATIORGA","Vinati Organics","Chemicals"),
    ("TATACHEM","Tata Chemicals","Chemicals"),("DEEPAKNITR","Deepak Nitrite","Chemicals"),
    ("FINEORG","Fine Organic Industries","Chemicals"),("CLEAN","Clean Science & Technology","Chemicals"),
    ("GAIL","GAIL India","Oil & Gas"),("IGL","Indraprastha Gas","Oil & Gas"),
    ("MGL","Mahanagar Gas","Oil & Gas"),("PETRONET","Petronet LNG","Oil & Gas"),
    ("GUJGASLTD","Gujarat Gas","Oil & Gas"),("SUZLON","Suzlon Energy","Renewable Energy"),
    ("KEC","KEC International","Infrastructure"),("KALPATPOWR","Kalpataru Power","Infrastructure"),
    ("OBEROIRLTY","Oberoi Realty","Real Estate"),("PHOENIXLTD","Phoenix Mills","Real Estate"),
    ("PRESTIGE","Prestige Estates","Real Estate"),("SOBHA","Sobha","Real Estate"),
    ("BRIGADE","Brigade Enterprises","Real Estate"),("JUBLFOOD","Jubilant Foodworks","QSR"),
    ("WESTLIFE","Westlife Foodworld","QSR"),("DEVYANI","Devyani International","QSR"),
    ("RELAXO","Relaxo Footwears","Consumer"),("BATAINDIA","Bata India","Consumer"),
    ("METROBRAND","Metro Brands","Retail"),("MANYAVAR","Vedant Fashions","Retail"),
    ("SYMPHONY","Symphony","Consumer"),("TTKPRESTIG","TTK Prestige","Consumer"),
    ("IRCON","IRCON International","Infrastructure"),("RITES","RITES","Infrastructure"),
    ("ZYDUSLIFE","Zydus Lifesciences","Pharma"),("NATCOPHARMA","Natco Pharma","Pharma"),
    ("GRANULES","Granules India","Pharma"),("SYNGENE","Syngene International","Pharma"),
    ("AJANTPHARM","Ajanta Pharma","Pharma"),("GLAND","Gland Pharma","Pharma"),
    ("ERIS","Eris Lifesciences","Pharma"),("JBCHEPHARM","JB Chemicals","Pharma"),
    ("BLUEDART","Blue Dart Express","Logistics"),("DELHIVERY","Delhivery","Logistics"),
    ("ALLCARGO","Allcargo Logistics","Logistics"),("VARUNBEV","Varun Beverages","FMCG"),
    ("RADICO","Radico Khaitan","Consumer"),("JSWENERGY","JSW Energy","Power"),
    ("ADANIGREEN","Adani Green Energy","Renewable Energy"),("ADANIPOWER","Adani Power","Power"),
    ("KALYNA","Kalyan Jewellers","Jewellery"),("SENCO","Senco Gold","Jewellery"),
    ("BEML","BEML","Defence"),("COCHINSHIP","Cochin Shipyard","Defence"),
    ("MAZDOCK","Mazagon Dock","Defence"),("GRSE","Garden Reach Shipbuilders","Defence"),
    ("SOLARINDS","Solar Industries India","Defence"),("DATAPATTNS","Data Patterns","Defence"),
    ("MTAR","MTAR Technologies","Defence"),("360ONE","360 ONE WAM","Broking"),
    ("NUVAMA","Nuvama Wealth Management","Broking"),("INDIGOPNTS","Indigo Paints","Consumer"),
    ("KANSAINER","Kansai Nerolac Paints","Consumer"),("SUNTV","Sun TV Network","Media"),
    ("ZEEL","Zee Entertainment","Media"),("PVRINOX","PVR Inox","Media"),
    ("BALRAMCHIN","Balrampur Chini Mills","Sugar"),("COROMANDEL","Coromandel International","Fertilizers"),
    ("ABSLAMC","Aditya Birla Sun Life AMC","AMC"),("UTI","UTI AMC","AMC"),
    ("HONAUT","Honeywell Automation","Capital Goods"),("WHIRLPOOL","Whirlpool India","Consumer"),
    ("LUMAXTECH","Lumax Technologies","Auto Ancillaries"),("LUMAXIND","Lumax Industries","Auto Ancillaries"),
    ("AVANTIFEED","Avanti Feeds","Food Processing"),("VIMTALABS","Vimta Labs","Healthcare"),
    ("GALLANTT","Gallantt Metal","Metals"),("SARDAEN","Sarda Energy & Minerals","Metals"),
    ("SHAILY","Shaily Engineering Plastics","Plastics"),("BLUEJET","Blue Jet Healthcare","Pharma"),
    ("ENDURANCE","Endurance Technologies","Auto Ancillaries"),("SONA","Sona BLW Precision","Auto Ancillaries"),
    ("GABRIEL","Gabriel India","Auto Ancillaries"),("EXIDEIND","Exide Industries","Auto Ancillaries"),
    ("CEATLTD","CEAT","Tyres"),("MRF","MRF","Tyres"),
    ("APOLLOTYRE","Apollo Tyres","Tyres"),("BALKRISIND","Balkrishna Industries","Tyres"),
    ("TATACOMM","Tata Communications","Telecom"),("HFCL","HFCL","Telecom"),
    ("MINDA","Minda Corporation","Auto Ancillaries"),("CRAFTSMAN","Craftsman Automation","Auto Ancillaries"),
    ("SUPRAJIT","Suprajit Engineering","Auto Ancillaries"),("SUBROS","Subros","Auto Ancillaries"),
    ("PRICOL","Pricol","Auto Ancillaries"),("ROLEX","Rolex Rings","Auto Ancillaries"),
    ("BOROSIL","Borosil Renewables","Renewable Energy"),("INOXWIND","Inox Wind","Renewable Energy"),
    ("GSFC","Gujarat State Fertilizers","Fertilizers"),("GNFC","GNFC","Chemicals"),
    ("NOCIL","NOCIL","Chemicals"),("LAXMIPATHI","Laxmi Organic Industries","Chemicals"),
    ("ANUPAM","Anupam Rasayan","Chemicals"),("EPIGRAL","Epigral","Chemicals"),
    ("CENTURYPLY","Century Plyboards","Building Materials"),("ASTRAL","Astral Poly Technik","Plastics"),
    ("FINOLEX","Finolex Industries","Plastics"),("GREENPANEL","Greenpanel Industries","Building Materials"),
    ("IDFCFIRSTB","IDFC First Bank","Banking"),("EQUITASBNK","Equitas Small Finance Bank","Banking"),
    ("UJJIVANSFB","Ujjivan Small Finance Bank","Banking"),("SURYODAY","Suryoday Small Finance Bank","Banking"),
    ("CITYUNIONBK","City Union Bank","Banking"),("KARURVYSYA","Karur Vysya Bank","Banking"),
    ("INDIANB","Indian Bank","Banking"),("DCBBANK","DCB Bank","Banking"),
    ("JUSTDIAL","Just Dial","Internet"),("INFIBEAM","Infibeam Avenues","Fintech"),
    ("POONAWALLA","Poonawalla Fincorp","NBFC"),("SPANDANA","Spandana Sphoorty","MFI"),
    ("IIFL","IIFL Finance","NBFC"),("SAREGAMA","Saregama India","Media"),
    ("TIPS","Tips Music","Media"),("WABAG","VA Tech Wabag","Infrastructure"),
    ("PNC","PNC Infratech","Infrastructure"),("KNRCON","KNR Constructions","Infrastructure"),
    ("HGINFRA","H.G. Infra Engineering","Infrastructure"),("MAHLIFE","Mahindra Lifespace","Real Estate"),
    ("SUNTECK","Sunteck Realty","Real Estate"),("NCLIND","NCL Industries","Cement"),
    ("HEIDELBERG","HeidelbergCement India","Cement"),("BIRLACORPN","Birla Corporation","Cement"),
    ("JKCEMENT","JK Cement","Cement"),("ELGI","Elgi Equipments","Capital Goods"),
    ("GRINDWELL","Grindwell Norton","Capital Goods"),("GMMPFAUDLR","GMM Pfaudler","Capital Goods"),
    ("HITACHIENERGY","Hitachi Energy India","Capital Goods"),
    ("ALKYLAMINE","Alkyl Amines Chemicals","Chemicals"),("TATVA","Tatva Chintan Pharma","Chemicals"),
    ("AEGISCHEM","Aegis Logistics","Oil & Gas"),("GSPL","Gujarat State Petronet","Oil & Gas"),
    ("LINDEINDIA","Linde India","Chemicals"),("INOXAIR","INOX Air Products","Chemicals"),
    ("RAINBOW","Rainbow Children's Medicare","Healthcare"),
    ("MARKSANS","Marksans Pharma","Pharma"),("CAPLIPOINT","Caplin Point Laboratories","Pharma"),
    ("SOLARA","Solara Active Pharma","Pharma"),("NEULANDLAB","Neuland Laboratories","Pharma"),
    ("STRIDES","Strides Pharma","Pharma"),("DCAL","Dishman Carbogen Amcis","Pharma"),
    ("WINDLAS","Windlas Biotech","Pharma"),("SEQUENT","SeQuent Scientific","Pharma"),
    ("GILLETTE","Gillette India","FMCG"),("COLPAL","Colgate-Palmolive India","FMCG"),
    ("TATACOFFEE","Tata Coffee","FMCG"),("CCL","CCL Products India","FMCG"),
    ("GLOBUSSP","Globus Spirits","Consumer"),("3MINDIA","3M India","Diversified"),
    ("IDEAFORGE","ideaForge Technology","Defence"),("PARAS","Paras Defence","Defence"),
    ("SHOPERSTOP","Shoppers Stop","Retail"),("VMART","V-Mart Retail","Retail"),
    ("BATAINDIA","Bata India","Consumer"),("RELAXO","Relaxo Footwears","Consumer"),
    ("WONDERLA","Wonderla Holidays","Tourism"),("HAWKINCOOK","Hawkins Cookers","Consumer"),
    ("RAJESHEXPO","Rajesh Exports","Jewellery"),("THANGAMAYL","Thangamayil Jewellery","Jewellery"),
    ("SJS","SJS Enterprises","Consumer"),("MOTHERSON","Samvardhana Motherson","Auto Ancillaries"),
    ("SUNDRAMFAST","Sundram Fasteners","Auto Ancillaries"),("AMARAJABAT","Amara Raja Energy","Auto Ancillaries"),
    ("SKFINDIA","SKF India","Auto Ancillaries"),("MAHINDCIE","Mahindra CIE Automotive","Auto Ancillaries"),
    ("NAUKRI","Info Edge","Internet"),("MANYAVAR","Vedant Fashions","Retail"),
    ("GEOJITFSL","Geojit Financial Services","Broking"),("KFINTECH","KFin Technologies","Fintech"),
    ("ABCAPITAL","Aditya Birla Capital","Finance"),("LTFH","L&T Finance Holdings","NBFC"),
    ("CHOLAFIN","Cholamandalam Investment","NBFC"),("SUNDARMFIN","Sundaram Finance","NBFC"),
    ("IIFLWAM","IIFL Wealth Management","Broking"),("GEOJITFSL","Geojit Financial Services","Broking"),
    ("HATHWAY","Hathway Cable","Media"),("ZUARI","Zuari Industries","Fertilizers"),
    ("RENUKA","Shree Renuka Sugars","Sugar"),("TRIVENI","Triveni Engineering","Sugar"),
    ("BALRAMCHIN","Balrampur Chini Mills","Sugar"),("DHAMPURSUG","Dhampur Sugar","Sugar"),
    ("SAPPHIRE","Sapphire Foods","QSR"),("BARBEQUE","Barbeque-Nation","QSR"),
    ("BENGALENE","Bengal Energy","Energy"),("PCJEWELLER","PC Jeweller","Jewellery"),
    ("KAYNES","Kaynes Technology","Electronics"),("SYRMA","Syrma SGS Technology","Electronics"),
    ("TCI","Transport Corporation","Logistics"),("VRL","VRL Logistics","Logistics"),
    ("GATI","Gati","Logistics"),("IRCON","IRCON International","Infrastructure"),
    ("NBCC","NBCC India","Infrastructure"),("SJVN","SJVN","Power"),
]

# Well-known BSE-only stocks (not listed on NSE) with scrip codes
_BSE_ONLY_FALLBACK = [
    # (scrip_code, company_name, industry)
    ("523395","Premco Global","Textiles"),
    ("532678","Tanla Platforms BSE","IT"),
    ("543272","Ami Organics","Chemicals"),
    ("543066","Route Mobile BSE","IT"),
    ("534091","Hester Biosciences","Pharma"),
    ("531270","Filatex India","Textiles"),
    ("506947","Tide Water Oil","Oil & Gas"),
    ("505537","Elgi Ultra Industries","Consumer"),
    ("517562","Shanthi Gears","Capital Goods"),
    ("519552","Sayaji Hotels","Hotels"),
    ("511176","Muthoot Capital Services","NBFC"),
    ("539236","Shankara Building Products","Building Materials"),
    ("543234","Nureca","Healthcare"),
    ("526517","Gokaldas Exports","Textiles"),
    ("531508","Usha Martin","Metals"),
    ("500113","Bombay Burmah Trading","Conglomerate"),
    ("500124","Dr. Reddy BSE","Pharma"),
    ("500340","Rubberex India","Consumer"),
    ("523204","Linc Pen & Plastics","Consumer"),
    ("543217","Antony Waste Handling","Infrastructure"),
    ("543260","Dodla Dairy","FMCG"),
    ("543251","Krishna Institute of Medical Sciences","Healthcare"),
    ("543252","Supriya Lifescience","Chemicals"),
    ("543258","Shyam Metalics","Metals"),
    ("543261","G R Infraprojects","Infrastructure"),
    ("543271","Tatva Chintan BSE","Chemicals"),
    ("543318","Zomato BSE","Internet"),
    ("543320","Clean Science BSE","Chemicals"),
    ("543354","Aptus Value Housing Finance","NBFC"),
    ("543398","Chemplast Sanmar","Chemicals"),
    ("543401","DevyaniInternational BSE","QSR"),
    ("543411","Glenmark Life Sciences","Pharma"),
    ("543428","Rolex Rings BSE","Auto Ancillaries"),
    ("543435","Nuvoco Vistas","Cement"),
    ("543509","Sigachi Industries","Pharma"),
    ("543521","Harsha Engineers International","Capital Goods"),
    ("543524","Syrma SGS BSE","Electronics"),
    ("543525","Bikaji Foods International","FMCG"),
    ("543526","Dreamfolks Services","Travel"),
    ("543528","Fusion Micro Finance","MFI"),
    ("543529","Global Health (Medanta)","Healthcare"),
    ("543530","Kaynes Technology BSE","Electronics"),
    ("543533","Sula Vineyards","Consumer"),
    ("543534","Landmark Cars","Auto Retail"),
    ("543537","Uniparts India","Auto Ancillaries"),
    ("543538","Archean Chemical Industries","Chemicals"),
    ("543540","Electronics Mart India","Retail"),
    ("543542","Dharmaj Crop Guard","Agro Chemicals"),
    ("543543","Abans Holdings","Finance"),
    ("543545","Elin Electronics","Electronics"),
    ("543546","Imageplus Global","Media"),
    ("543547","Keystone Realtors","Real Estate"),
    ("543548","Bikaji Foods BSE","FMCG"),
    ("543568","Yatharth Hospital","Healthcare"),
    ("543580","Senores Pharmaceuticals","Pharma"),
    ("543599","Veranda Learning Solutions","Education"),
    ("543620","Prudent Corporate Advisory","Finance"),
    ("543635","Aadhaar Housing Finance","NBFC"),
    ("543643","Navi Technologies","Fintech"),
    ("543658","Aether Industries","Chemicals"),
    ("543659","Delhivery BSE","Logistics"),
    ("543660","Emcure Pharmaceuticals","Pharma"),
    ("543675","EMS","Infrastructure"),
    ("543695","Jana Small Finance Bank","Banking"),
    ("543697","RBA Finance and Investment","Finance"),
    ("543722","Penna Cement","Cement"),
    ("543750","Cello World","Consumer"),
    ("543769","Mankind Pharma","Pharma"),
    ("543801","Signature Global India","Real Estate"),
    ("543815","Plaza Wires","Electricals"),
    ("543817","Indian Renewable Energy Development Agency","Finance"),
    ("543820","Jyoti CNC Automation","Capital Goods"),
    ("543833","Bajaj Housing Finance","NBFC"),
    ("543836","Premier Energies","Renewable Energy"),
    ("543838","Ola Electric Mobility","EV"),
    ("543860","Orient Technologies","IT"),
    ("543867","Interarch Building Products","Building Materials"),
    ("543868","Jeevant Medical Systems","Healthcare"),
    ("543896","NTPC Green Energy","Renewable Energy"),
    ("543897","Swiggy","Internet"),
    ("543898","Vishal Mega Mart","Retail"),
    ("543899","Sai Life Sciences","Pharma"),
    ("543900","One Mobikwik Systems","Fintech"),
    ("543901","Inventurus Knowledge Solutions","Healthcare IT"),
    ("543904","Acme Solar Holdings","Renewable Energy"),
    ("543905","Afcons Infrastructure","Infrastructure"),
    ("543906","Waaree Energies","Renewable Energy"),
    ("543907","Hyundai Motor India","Auto"),
    ("543908","Garuda Construction","Infrastructure"),
    ("543909","Diffusion Engineers","Capital Goods"),
    ("543910","Deepak Builders & Engineers India","Infrastructure"),
]

def _hardcoded_fallback() -> pd.DataFrame:
    nse_rows = []
    for symbol, company_name, industry in _NSE_FALLBACK:
        nse_rows.append({
            "ticker_yf":    symbol + ".NS",
            "display_name": company_name + " (NSE)",
            "company_name": company_name,
            "exchange":     "NSE",
            "symbol":       symbol,
            "isin":         "",
            "industry":     industry,
        })

    bse_rows = []
    for scrip_code, company_name, industry in _BSE_ONLY_FALLBACK:
        bse_rows.append({
            "ticker_yf":    scrip_code + ".BO",
            "display_name": company_name + " (BSE)",
            "company_name": company_name,
            "exchange":     "BSE",
            "symbol":       scrip_code,
            "isin":         "",
            "industry":     industry,
        })

    df = pd.DataFrame(nse_rows + bse_rows)
    df = df.drop_duplicates(subset="ticker_yf")
    print(f"  ✓ Hardcoded fallback: {len(df)} stocks ({len(nse_rows)} NSE + {len(bse_rows)} BSE-only)")
    return df


# ═══════════════════════════════════════════════════════════════════
#  DEDUP: merge NSE + BSE, prefer NSE for dual-listed stocks
# ═══════════════════════════════════════════════════════════════════

def _merge_exchanges(nse_df: pd.DataFrame | None, bse_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Combine NSE + BSE rows using Model B (company-level universe).
    For dual-listed stocks (same ISIN), keep only the NSE ticker.
    BSE-only stocks (ISIN not present on NSE) are retained.
    Rows with no ISIN are kept as-is (no ISIN dedup applied to them).
    """
    parts = []
    if nse_df is not None and not nse_df.empty:
        parts.append(nse_df)
    if bse_df is not None and not bse_df.empty:
        parts.append(bse_df)

    if not parts:
        return _hardcoded_fallback()

    combined = pd.concat(parts, ignore_index=True)

    # ISIN dedup: prefer NSE for dual-listed stocks
    # Split rows that have a non-empty ISIN from those that don't
    has_isin_mask = combined["isin"].notna() & (combined["isin"].str.strip() != "")
    has_isin  = combined[has_isin_mask].copy()
    no_isin   = combined[~has_isin_mask].copy()

    # Sort so "BSE" < "NSE" alphabetically — keep="last" retains NSE
    deduped = (
        has_isin
        .sort_values("exchange")
        .drop_duplicates(subset="isin", keep="last")
    )

    combined = pd.concat([deduped, no_isin], ignore_index=True)
    combined = combined.drop_duplicates(subset="ticker_yf")
    combined = combined.reset_index(drop=True)
    return combined


# ═══════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════

def build_universe(force: bool = False, progress_callback=None) -> pd.DataFrame:
    """
    Build universe.parquet from NSE + BSE sources.
    Returns DataFrame with full universe.
    progress_callback(step: str, detail: str) → optional UI feedback.
    """
    if not force and os.path.exists(UNIVERSE_FILE):
        age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(UNIVERSE_FILE))).days
        if age < UNIVERSE_TTL_DAYS:
            return pd.read_parquet(UNIVERSE_FILE)

    def _cb(step, detail=""):
        if progress_callback:
            progress_callback(step, detail)
        print(f"  [{step}] {detail}")

    _cb("start", "Building NSE + BSE universe…")

    # ── NSE ──
    _cb("nse", "Fetching NSE equity list…")
    nse_df = _fetch_nse()
    if nse_df is None or len(nse_df) < 100:
        _cb("nse_fallback", "NSE API failed — using hardcoded NSE list")
        nse_df = None   # will merge with BSE below; fallback applied in merge

    # ── BSE ──
    _cb("bse", "Fetching BSE equity list…")
    bse_df = _fetch_bse()
    if bse_df is None or len(bse_df) < 100:
        _cb("bse_fallback", "BSE API failed — using hardcoded BSE list")
        bse_df = None

    # ── Merge ──
    _cb("merge", "Merging exchanges…")
    if nse_df is None and bse_df is None:
        combined = _hardcoded_fallback()
    else:
        combined = _merge_exchanges(nse_df, bse_df)
        # If only one source succeeded, patch in the hardcoded rows for the missing exchange
        if nse_df is None or bse_df is None:
            hc = _hardcoded_fallback()
            missing_exchange = "NSE" if nse_df is None else "BSE"
            hc_part = hc[hc["exchange"] == missing_exchange]
            # Add hardcoded rows that aren't already in combined
            existing = set(combined["ticker_yf"].tolist())
            new_rows = hc_part[~hc_part["ticker_yf"].isin(existing)]
            combined = pd.concat([combined, new_rows], ignore_index=True)

    combined = combined.drop_duplicates(subset="ticker_yf").reset_index(drop=True)

    # ── Save ──
    combined.to_parquet(UNIVERSE_FILE, index=False)
    nse_count = (combined["exchange"] == "NSE").sum()
    bse_count = (combined["exchange"] == "BSE").sum()
    _cb("done", f"Universe saved: {len(combined)} stocks ({nse_count} NSE + {bse_count} BSE)")
    return combined


def load_universe() -> pd.DataFrame:
    """Load universe from disk (builds if missing)."""
    if os.path.exists(UNIVERSE_FILE):
        try:
            return pd.read_parquet(UNIVERSE_FILE)
        except Exception:
            pass
    return build_universe()


def universe_is_fresh() -> bool:
    if not os.path.exists(UNIVERSE_FILE):
        return False
    age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(UNIVERSE_FILE))).days
    return age < UNIVERSE_TTL_DAYS


def universe_stats() -> dict:
    """Return quick stats about the current universe file."""
    if not os.path.exists(UNIVERSE_FILE):
        return {"exists": False}
    try:
        df  = pd.read_parquet(UNIVERSE_FILE)
        age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(UNIVERSE_FILE))).days
        return {
            "exists":    True,
            "total":     len(df),
            "nse":       int((df["exchange"] == "NSE").sum()),
            "bse":       int((df["exchange"] == "BSE").sum()),
            "age_days":  age,
            "fresh":     age < UNIVERSE_TTL_DAYS,
        }
    except Exception:
        return {"exists": True, "total": 0, "nse": 0, "bse": 0, "age_days": 999, "fresh": False}


if __name__ == "__main__":
    df = build_universe(force=True)
    print(f"\nTotal: {len(df)} stocks")
    print(df.groupby("exchange").size())
    print(df.head(5).to_string(index=False))
