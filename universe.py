import logging, os
from typing import List
import httpx

log = logging.getLogger("universe")
KUCOIN_BASE_URL = os.getenv("KUCOIN_BASE_URL","https://api-futures.kucoin.com")

def discover_symbols(limit: int = 450) -> List[str]:
    try:
        url = f"{KUCOIN_BASE_URL}/api/v1/contracts/active"
        with httpx.Client(timeout=float(os.getenv("HTTP_TIMEOUT","10"))) as c:
            r = c.get(url)
        js = r.json().get("data", [])
        syms = []
        for it in js:
            s = str(it.get("symbol","")).upper()
            if not s.endswith("USDTM"):
                continue
            if it.get("status") != "Open":
                continue
            syms.append(s)
        syms = sorted(set(syms))[:limit]
        log.info("discovered %d symbols", len(syms))
        return syms
    except Exception as e:
        log.error("discover_symbols KO: %s", e)
        return []

def load_universe(auto: bool, env_syms: List[str], limit: int) -> List[str]:
    if env_syms:
        log.info("universe from env: %d symbols", len(env_syms))
        return env_syms[:limit]
    if auto:
        return discover_symbols(limit=limit)
    return ["BTCUSDTM","ETHUSDTM","SOLUSDTM"]
