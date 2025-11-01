"""
fills.py — suivi d'exécution KuCoin Futures (polling)
- wait_for_fill(order_id, timeout_s=20) -> {"filled": bool, "filled_size": int, "price": float, "raw": ...}
"""
from __future__ import annotations
import time, requests, json, base64, hmac, hashlib
from settings import KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE

BASE = "https://api-futures.kucoin.com"

def _sign(ts, method, ep, body):
    payload = str(ts) + method.upper() + ep + (json.dumps(body) if body else "")
    sig = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    pph = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), KUCOIN_API_PASSPHRASE.encode(), hashlib.sha256).digest())
    return sig, pph

def _headers(ts, sig, pph):
    return {
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": str(ts),
        "KC-API-KEY": KUCOIN_API_KEY,
        "KC-API-PASSPHRASE": pph,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json",
    }

def get_order(order_id: str) -> dict:
    ts = int(time.time()*1000)
    ep = f"/api/v1/orders/{order_id}"
    sig, pph = _sign(ts, "GET", ep, None)
    r = requests.get(BASE+ep, headers=_headers(ts, sig, pph), timeout=8)
    try:
        return r.json()
    except Exception:
        return {"raw": r.text, "status": r.status_code}

def wait_for_fill(order_id: str, timeout_s: int = 20, poll_ms: int = 300) -> dict:
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_s:
        d = get_order(order_id)
        last = d
        try:
            data = d.get("data") or {}
            status = (data.get("status") or "").lower()  # "open","match","done","cancelled"
            filled = float(data.get("dealSize") or 0.0)
            if filled > 0 or status in ("match","done","filled"):
                return {
                    "filled": True,
                    "filled_size": int(round(filled)),
                    "price": float(data.get("price") or 0.0),
                    "raw": d,
                }
        except Exception:
            pass
        time.sleep(poll_ms/1000.0)
    return {"filled": False, "filled_size": 0, "price": 0.0, "raw": last}
