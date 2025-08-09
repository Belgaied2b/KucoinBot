import time
import httpx

BASE = "https://fapi.binance.com"  # Binance Futures (USDT-M)

def get_funding_rate(symbol: str) -> float:
    try:
        r = httpx.get(f"{BASE}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=5.0)
        if r.status_code==200:
            return float(r.json().get("lastFundingRate", 0.0))
    except: pass
    return 0.0

def get_open_interest(symbol: str) -> float:
    try:
        r = httpx.get(f"{BASE}/futures/data/openInterestHist", params={"symbol": symbol, "period": "5m", "limit": 2}, timeout=5.0)
        if r.status_code==200:
            arr = r.json()
            if len(arr)>=1: return float(arr[-1]["sumOpenInterest"])
    except: pass
    return 0.0

def get_recent_liquidations(symbol: str, minutes: int = 5) -> float:
    try:
        now = int(time.time()*1000)
        start = now - minutes*60*1000
        r = httpx.get(f"{BASE}/fapi/v1/allForceOrders", params={"symbol": symbol, "startTime": start, "limit": 1000}, timeout=5.0)
        if r.status_code==200:
            tot = 0.0
            for it in r.json():
                tot += float(it.get("origQty",0.0))*float(it.get("price",0.0))
            return tot
    except: pass
    return 0.0

# --- MACRO (gratuit via CoinGecko simple endpoints publics) ---
CG = "https://api.coingecko.com/api/v3"
def get_macro_total_mcap() -> float:
    try:
        r = httpx.get(f"{CG}/global", timeout=6.0)
        if r.status_code==200:
            return float(r.json().get("data",{}).get("total_market_cap",{}).get("usd",0.0))
    except: pass
    return 0.0

def get_macro_btc_dominance() -> float:
    try:
        r = httpx.get(f"{CG}/global", timeout=6.0)
        if r.status_code==200:
            return float(r.json().get("data",{}).get("market_cap_percentage",{}).get("btc",0.0))/100.0
    except: pass
    return 0.0

def get_macro_total2() -> float:
    tot = get_macro_total_mcap()
    dom = get_macro_btc_dominance()
    return max(0.0, tot * (1.0 - dom))
