import os, time, hmac, hashlib, base64, json, logging
import httpx

log = logging.getLogger("kucoin")

BASE = os.getenv("KUCOIN_BASE_URL", "https://api-futures.kucoin.com")
KEY  = os.getenv("KUCOIN_KEY", "")
SEC  = os.getenv("KUCOIN_SECRET", "")
PAS  = os.getenv("KUCOIN_PASSPHRASE", "")

TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
DRY_RUN = os.getenv("DRY_RUN", "0").lower() in ("1","true","t","yes","on")

def _tsms() -> str: return str(int(time.time() * 1000))

def _sign(ts: str, method: str, path: str, body: str) -> dict:
    msg = f"{ts}{method.upper()}{path}{body}".encode()
    sig = base64.b64encode(hmac.new(SEC.encode(), msg, hashlib.sha256).digest()).decode()
    pph = base64.b64encode(hmac.new(SEC.encode(), PAS.encode(), hashlib.sha256).digest()).decode()
    return {"KC-API-SIGN": sig, "KC-API-PASSPHRASE": pph}

def _headers(ts: str, sig: dict) -> dict:
    return {
        "KC-API-KEY": KEY, "KC-API-TIMESTAMP": ts, "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json", **sig
    }

def _req(method: str, path: str, payload: dict | None = None) -> dict:
    body = "" if payload is None else json.dumps(payload, separators=(",",":"), ensure_ascii=False)
    ts = _tsms()
    sig = _sign(ts, method, path, body)
    url = f"{BASE}{path}"
    log.info("REQ %s %s %s", method, path, body[:400])
    if DRY_RUN and method.upper()=="POST":
        fake = {"ok": True, "code": "200000", "data": {"orderId": f"dry_{int(time.time())}"}}
        log.info("DRY-RUN -> %s", fake)
        return fake
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.request(method.upper(), url, headers=_headers(ts, sig), content=body)
    log.info("RESP %s %s", r.status_code, r.text[:800])
    try:
        return r.json()
    except Exception:
        return {"code": str(r.status_code), "raw": r.text}

def get_symbol_meta(symbol: str) -> dict:
    path = "/api/v1/contracts/active"
    data = _req("GET", path)
    if isinstance(data, dict) and data.get("data"):
        for it in data["data"]:
            if str(it.get("symbol","")).upper()==symbol.upper():
                return it
    return {}

def fetch_klines(symbol: str, interval: str="1h", limit: int=500):
    m = {"1h":"1hour","4h":"4hour","1d":"1day","15m":"15min"}
    typ = m.get(interval, "1hour")
    path=f"/api/v1/kline/query?symbol={symbol}&granularity={typ}&limit={int(limit)}"
    data=_req("GET", path)
    import pandas as pd
    cols=["time","open","close","high","low","volume","turnover"]
    try:
        df = pd.DataFrame(data.get("data", []), columns=cols)
        for c in cols[1:]: df[c]=df[c].astype(float)
        df["time"]=df["time"].astype("int64")
        return df
    except Exception:
        return None

def place_limit_order(symbol: str, side: str, price: float, value_usdt: float,
                      leverage: int = 5, post_only: bool = True,
                      sl: float | None = None, tp1: float | None = None, tp2: float | None = None) -> dict:
    payload = {
        "clientOid": str(int(time.time()*1000)),
        "symbol": symbol,
        "side": side.lower(),
        "type": "limit",
        "price": f"{price}",
        "value": f"{float(value_usdt)}",
        "leverage": str(leverage),
        "timeInForce": "GTC",
        "postOnly": bool(post_only),
    }
    return _req("POST", "/api/v1/orders", payload)
