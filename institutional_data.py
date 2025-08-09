import time
import httpx
from logger_utils import get_logger

_logger = get_logger("institutional_data")

def _log_info(msg: str):
    try: _logger.info(msg)
    except Exception: pass
    print(msg, flush=True)

def _log_warn(msg: str):
    try: _logger.warning(msg)
    except Exception: pass
    print(msg, flush=True)

def _log_exc(prefix: str, e: Exception):
    try: _logger.exception(f"{prefix} error: {e}")
    except Exception: pass
    print(f"{prefix} error: {e}", flush=True)

BASE = "https://fapi.binance.com"  # Binance Futures (USDT-M)

def map_symbol_to_binance(sym: str) -> str:
    """Convertit un symbole KuCoin vers le format Binance Futures USDT-M."""
    s = (sym or "").upper()
    if s.endswith("USDTM"):
        s = s.replace("USDTM", "USDT")
    if s.endswith(".P"):
        s = s.replace(".P", "")
    return s

def get_funding_rate(symbol: str) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = httpx.get(f"{BASE}/fapi/v1/premiumIndex", params={"symbol": b_symbol}, timeout=6.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[Funding] {b_symbol} data={data} ({ms:.1f} ms)")
            return float(data.get("lastFundingRate", 0.0) or 0.0)
        _log_warn(f"[Funding] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc(f"[Funding] {b_symbol}", e)
    return 0.0

def get_open_interest(symbol: str) -> float:
    """
    OI instantané (contrats) — endpoint simple & public.
    Doc: GET /fapi/v1/openInterest -> {"openInterest":"12345.678","symbol":"BTCUSDT","time":...}
    """
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = httpx.get(f"{BASE}/fapi/v1/openInterest", params={"symbol": b_symbol}, timeout=6.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[OI] {b_symbol} data={data} ({ms:.1f} ms)")
            return float(data.get("openInterest", 0.0) or 0.0)
        _log_warn(f"[OI] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc(f"[OI] {b_symbol}", e)
    return 0.0

def get_recent_liquidations(symbol: str, minutes: int = 5) -> float:
    """
    Somme notionnelle liquidée sur la fenêtre récente (approx).
    Doc: GET /fapi/v1/allForceOrders (public)
    """
    b_symbol = map_symbol_to_binance(symbol)
    try:
        now = int(time.time() * 1000)
        start = now - minutes * 60 * 1000
        t0 = time.time()
        r = httpx.get(
            f"{BASE}/fapi/v1/allForceOrders",
            params={"symbol": b_symbol, "startTime": start, "limit": 1000},
            timeout=6.0,
        )
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            tot = 0.0
            for it in data:
                # approx notionnelle: qty * price
                try:
                    tot += float(it.get("origQty", 0.0) or 0.0) * float(it.get("price", 0.0) or 0.0)
                except Exception:
                    continue
            _log_info(f"[Liq] {b_symbol} {len(data)} orders, notionnel≈{tot:.2f} ({ms:.1f} ms)")
            return tot
        _log_warn(f"[Liq] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc(f"[Liq] {b_symbol}", e)
    return 0.0

# --- MACRO (CoinGecko gratuit) ---
CG = "https://api.coingecko.com/api/v3"

def get_macro_total_mcap() -> float:
    try:
        t0 = time.time()
        r = httpx.get(f"{CG}/global", timeout=8.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[Macro] TOTAL MCAP {data} ({ms:.1f} ms)")
            return float(data.get("data", {}).get("total_market_cap", {}).get("usd", 0.0) or 0.0)
        _log_warn(f"[Macro] TOTAL MCAP HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc("[Macro] TOTAL MCAP", e)
    return 0.0

def get_macro_btc_dominance() -> float:
    try:
        t0 = time.time()
        r = httpx.get(f"{CG}/global", timeout=8.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[Macro] BTC DOM {data} ({ms:.1f} ms)")
            dom_pct = float(data.get("data", {}).get("market_cap_percentage", {}).get("btc", 0.0) or 0.0)
            return dom_pct / 100.0
        _log_warn(f"[Macro] BTC DOM HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc("[Macro] BTC DOM", e)
    return 0.0

def get_macro_total2() -> float:
    tot = get_macro_total_mcap()
    dom = get_macro_btc_dominance()
    return max(0.0, tot * (1.0 - dom))
