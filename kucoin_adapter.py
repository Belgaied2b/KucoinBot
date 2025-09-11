# kucoin_adapter.py — module "env-based" (Railway)
# - Lit les clés via env: KUCOIN_API_KEY / KUCOIN_API_SECRET / KUCOIN_API_PASSPHRASE
# - Signature V2 (TIMESTAMP + METHOD + PATH + BODY)
# - Ordres envoyés comme ton code brut (sans postOnly forcé, leverage int, price simple)
# - Retries: 100001 (leverage fallback), 300012 (clamp prix), 330011 (si hedge détecté)
# - Logs masqués des clés + DEBUG activable via KC_VERBOSE=1

import os, time, hmac, base64, hashlib, logging
from typing import Any, Dict, Optional, Tuple

import httpx
import ujson as json

# ====== LOGGING ======
log = logging.getLogger("kucoin.adapter")
if not log.handlers:
    _h = logging.StreamHandler()
    _fmt = logging.Formatter("%(asctime)s | %(levelname)5s | %(name)s | [-] %(message)s")
    _h.setFormatter(_fmt)
    log.addHandler(_h)

KC_VERBOSE = os.getenv("KC_VERBOSE", "0") == "1"
log.setLevel(logging.DEBUG if KC_VERBOSE else logging.INFO)

# ====== CONFIG (env) ======
def _mask(s: Optional[str]) -> str:
    if not s:
        return "MISSING"
    s = str(s)
    if len(s) <= 8:
        return s[0] + "***" + s[-1]
    return s[:4] + "..." + s[-4:]

def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        log.error("ENV %s manquante. Vérifie Railway → Variables.", name)
        raise RuntimeError(f"Missing required env var: {name}")
    return v

KUCOIN_API_KEY        = _require_env("KUCOIN_API_KEY")
KUCOIN_API_SECRET     = _require_env("KUCOIN_API_SECRET")
KUCOIN_API_PASSPHRASE = _require_env("KUCOIN_API_PASSPHRASE")

log.info("KUCOIN_API_KEY=%s KUCOIN_API_SECRET=%s KUCOIN_API_PASSPHRASE=%s",
         _mask(KUCOIN_API_KEY), _mask(KUCOIN_API_SECRET), _mask(KUCOIN_API_PASSPHRASE))

BASE = os.getenv("KUCOIN_BASE_URL", "https://api-futures.kucoin.com").rstrip("/")
ORDERS_PATH = "/api/v1/orders"
TIME_PATH   = "/api/v1/timestamp"

DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "10"))
HTTP_TIMEOUT     = float(os.getenv("HTTP_TIMEOUT_SEC", "10.0"))

# ====== TIME SYNC ======
_SERVER_OFFSET = 0.0
def _sync_server_time() -> None:
    global _SERVER_OFFSET
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(BASE + TIME_PATH)
            js = r.json()
            if r.status_code == 200:
                server_ms = int(js.get("data", 0))
                _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
                log.info("[time] offset=%.3fs http=200", _SERVER_OFFSET)
    except Exception as e:
        log.warning("time sync failed: %s", e)

def _ts_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

# ====== SIGNATURE V2 ======
def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

def _headers(method: str, path: str, body_str: str = "") -> Dict[str, str]:
    ts = str(_ts_ms())
    sig = _b64_hmac_sha256(KUCOIN_API_SECRET, ts + method.upper() + path + (body_str or ""))
    psp = _b64_hmac_sha256(KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
    return {
        "KC-API-KEY": KUCOIN_API_KEY,
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": psp,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json"
    }

# ====== HTTP ======
def _post(path: str, body: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    body_str = json.dumps(body, separators=(",", ":"))
    hdrs = _headers("POST", path, body_str)
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as c:
            r = c.post(url, headers=hdrs, content=body_str.encode("utf-8"))
            js = r.json()
            return r.status_code == 200, js
    except Exception as e:
        return False, {"error": str(e)}

# ====== PLACE LIMIT ======
def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    value_usdt: float = 20.0,
    client_order_id: Optional[str] = None,
    leverage: Optional[int] = None,
    post_only: bool = False  # par défaut désactivé
) -> Dict[str, Any]:
    _sync_server_time()

    lev = int(leverage or DEFAULT_LEVERAGE)
    value_qty = round(value_usdt * lev, 4)

    body = {
        "clientOid": client_order_id or str(_ts_ms()),
        "symbol": symbol,
        "side": side.lower(),
        "leverage": lev,
        "type": "limit",
        "price": str(price),
        "valueQty": str(value_qty),
        "timeInForce": "GTC",
    }
    if post_only:
        body["postOnly"] = True

    log.info("[place_limit] %s %s px=%s valueQty=%s lev=%s postOnly=%s",
             symbol, side, price, value_qty, lev, post_only)

    ok, js = _post(ORDERS_PATH, body)
    if ok and js.get("code") == "200000":
        oid = js["data"].get("orderId")
        log.info("✅ Order placed %s %s id=%s", symbol, side, oid)
        return {"ok": True, "orderId": oid, "resp": js}
    else:
        log.error("❌ Order failed %s %s resp=%s", symbol, side, js)
        return {"ok": False, "resp": js}
