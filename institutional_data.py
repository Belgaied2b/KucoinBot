import time
import httpx
from logger_utils import get_logger

log = get_logger("institutional_data")

BASE = "https://fapi.binance.com"  # Binance Futures (USDT-M)

def map_symbol_to_binance(sym: str) -> str:
    """Convertit un symbole KuCoin vers le format Binance Futures USDT-M."""
    s = sym.upper()
    if s.endswith("USDTM"):
        s = s.replace("USDTM", "USDT")
    elif s.endswith(".P"):
        s = s.replace(".P", "")  # Si jamais format perp
    return s

def get_funding_rate(symbol: str) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = httpx.get(f"{BASE}/fapi/v1/premiumIndex", params={"symbol": b_symbol}, timeout=5.0)
        elapsed = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            log.info(f"[Funding] {b_symbol} data={data} ({elapsed:.1f} ms)")
            return float(data.get("lastFundingRate", 0.0))
        else:
            log.warning(f"[Funding] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        log.exception(f"[Funding] {b_symbol} error: {e}")
    return 0.0

def get_open_interest(symbol: str) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = httpx.get(f"{BASE}/futures/data/openInterestHist", params={"symbol": b_symbol, "period": "5m", "limit": 2}, timeout=5.0)
        elapsed = (time.time() - t0) * 1000
        if r.status_code == 200:
            arr = r.json()
            log.info(f"[OI] {b_symbol} data={arr} ({elapsed:.1f} ms)")
            if len(arr) >= 1:
                return float(arr[-1].get("sumOpenInterest", 0.0))
        else:
            log.warning(f"[OI] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        log.exception(f"[OI] {b_symbol} error: {e}")
    return 0.0

def get_recent_liquidations(symbol: str, minutes: int = 5) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        now = int(time.time() * 1000)
        start = now - minutes * 60 * 1000
        t0 = time.time()
        r = httpx.get(f"{BASE}/fapi/v1/allForceOrders", params={"symbol": b_symbol, "startTime": start, "limit": 1000}, timeout=5.0)
        elapsed = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            log.info(f"[Liq] {b_symbol} {len(data)} orders ({elapsed:.1f} ms)")
            tot = 0.0
            for it in data:
                tot += float(it.get("origQty", 0.0)) * float(it.get("price", 0.0))
            return tot
        else:
            log.warning(f"[Liq] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        log.exception(f"[Liq] {b_symbol} error: {e}")
    return 0.0

# --- MACRO (CoinGecko gratuit) ---
CG = "https://api.coingecko.com/api/v3"

def get_macro_total_mcap() -> float:
    try:
        t0 = time.time()
        r = httpx.get(f"{CG}/global", timeout=6.0)
        elapsed = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            log.info(f"[Macro] TOTAL MCAP {data} ({elapsed:.1f} ms)")
            return float(data.get("data", {}).get("total_market_cap", {}).get("usd", 0.0))
        else:
            log.warning(f"[Macro] TOTAL MCAP HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        log.exception(f"[Macro] TOTAL MCAP error: {e}")
    return 0.0

def get_macro_btc_dominance() -> float:
    try:
        t0 = time.time()
        r = httpx.get(f"{CG}/global", timeout=6.0)
        elapsed = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            log.info(f"[Macro] BTC DOM {data} ({elapsed:.1f} ms)")
            return float(data.get("data", {}).get("market_cap_percentage", {}).get("btc", 0.0)) / 100.0
        else:
            log.warning(f"[Macro] BTC DOM HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        log.exception(f"[Macro] BTC DOM error: {e}")
    return 0.0

def get_macro_total2() -> float:
    tot = get_macro_total_mcap()
    dom = get_macro_btc_dominance()
    return max(0.0, tot * (1.0 - dom))
