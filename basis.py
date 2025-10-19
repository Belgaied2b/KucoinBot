import logging, httpx, os, time
log = logging.getLogger("basis")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT","10"))

def perp_basis(symbol: str) -> float:
    # Try Binance index & mark price as proxy; map symbol to Binance format
    b = symbol.upper().replace("USDTM","USDT")
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as c:
            idx = c.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": b}).json()
        mark = float(idx.get("markPrice",0.0)); idxp = float(idx.get("indexPrice",0.0))
        if mark>0 and idxp>0:
            return (mark-idxp)/idxp
    except Exception as e:
        log.warning("basis fail %s: %s", symbol, e)
    return 0.0

def term_structure_hint(symbol: str) -> float:
    # If quarterly fut exists: (quarterly - perp)/perp; placeholder using same endpoint if not available
    return 0.0
