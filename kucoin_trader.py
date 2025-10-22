"""
kucoin_trader.py — LIMIT orders en lots + modes auto
- Calcule 'size' (lots) à partir de MARGIN_USDT * LEVERAGE et des specs contrat (multiplier, lotSize).
- Aligne les modes avant l'ordre :
    * PositionMode: One-Way (0) par défaut (modifiable)
    * MarginMode (par symbole): ISOLATED par défaut (modifiable)
- Arrondit le prix au tickSize du contrat.
- Envoie clientOid (UUID v4).
"""
import os
import time
import hmac
import json
import uuid
import base64
import hashlib
import logging
from typing import Dict, Any

import requests

from settings import KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, MARGIN_USDT, LEVERAGE
from retry_utils import backoff_retry, TransientHTTPError
from kucoin_utils import get_contract_info

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"

# Endpoints
ORDERS_EP = "/api/v1/orders"
GET_POSITION_MODE_EP = "/api/v2/position/getPositionMode"
SWITCH_POSITION_MODE_EP = "/api/v2/position/switchPositionMode"   # body: {"positionMode":"0|1"}
SWITCH_MARGIN_MODE_EP = "/api/v2/position/changeMarginMode"       # body: {"symbol":"XBTUSDTM","marginMode":"ISOLATED|CROSS"}

DEFAULT_POSITION_MODE = "0"       # "0"=one-way, "1"=hedge
DEFAULT_MARGIN_MODE = "ISOLATED"  # ou "CROSS" si tu préfères


# ------------- Sign / headers
def _sign(ts_ms: int, method: str, endpoint: str, body: dict | None) -> tuple[bytes, bytes]:
    payload = str(ts_ms) + method.upper() + endpoint + (json.dumps(body) if body else "")
    sig = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    pph = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), KUCOIN_API_PASSPHRASE.encode(), hashlib.sha256).digest())
    return sig, pph


def _headers(ts_ms: int, sig: bytes, pph: bytes) -> dict:
    return {
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": str(ts_ms),
        "KC-API-KEY": KUCOIN_API_KEY,
        "KC-API-PASSPHRASE": pph,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json",
    }


def _needs_retry(status_code: int) -> bool:
    return status_code >= 500


def _auth_post(endpoint: str, body: dict, timeout=12) -> requests.Response:
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "POST", endpoint, body)
    headers = _headers(ts, sig, pph)
    return requests.post(BASE + endpoint, headers=headers, json=body, timeout=timeout)


def _auth_get(endpoint: str, params=None, timeout=12) -> requests.Response:
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "GET", endpoint, None)
    headers = _headers(ts, sig, pph)
    return requests.get(BASE + endpoint, headers=headers, params=params, timeout=timeout)


def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


# ------------- Modes helpers
def _ensure_position_mode(target_mode: str = DEFAULT_POSITION_MODE) -> None:
    try:
        r = _auth_get(GET_POSITION_MODE_EP)
        if r.status_code != 200:
            LOGGER.warning("GetPositionMode status=%s body=%s", r.status_code, r.text)
            return
        cur = _safe_json(r).get("data", {}).get("positionMode")
        if str(cur) != str(target_mode):
            LOGGER.info("Switch positionMode %s -> %s", cur, target_mode)
            rr = _auth_post(SWITCH_POSITION_MODE_EP, {"positionMode": str(target_mode)})
            if rr.status_code != 200:
                LOGGER.error("SwitchPositionMode failed %s: %s", rr.status_code, rr.text)
    except Exception as e:
        LOGGER.exception("ensure_position_mode error: %s", e)


def _ensure_margin_mode(symbol: str, target_mode: str = DEFAULT_MARGIN_MODE) -> None:
    """
    Aligne le marginMode du symbole. Attention: KuCoin exige qu'il n'y ait pas
    d'ordres/positions ouverts pour changer — ici on tente en best-effort.
    """
    try:
        rr = _auth_post(SWITCH_MARGIN_MODE_EP, {"symbol": symbol, "marginMode": target_mode})
        if rr.status_code != 200:
            # Beaucoup de cas renvoient 400 avec message explicite, on log seulement.
            LOGGER.warning("SwitchMarginMode %s => %s failed %s: %s", symbol, target_mode, rr.status_code, rr.text)
    except Exception as e:
        LOGGER.exception("ensure_margin_mode error: %s", e)


# ------------- Sizing helpers
def _round_price(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    # arrondi au multiple inférieur de tick
    steps = int(price / tick)
    return round(steps * tick, 8)


def _compute_lots_for_value(price: float, multiplier: float, lot_size: int, budget_notional: float) -> int:
    """
    price: prix du contrat (USDT)
    multiplier: base-coin par lot (ex: 0.001 BTC)
    lot_size: lot minimal (entier, souvent 1)
    budget_notional: budget notionnel = MARGIN_USDT * LEVERAGE
    Retourne un entier de lots >= lot_size.
    """
    if price <= 0 or multiplier <= 0:
        return lot_size
    # notional d'1 lot = price * multiplier
    notional_per_lot = price * multiplier
    est = int(budget_notional / max(notional_per_lot, 1e-12))
    lots = max(lot_size, est)
    return lots


# ------------- Place order
@backoff_retry(exceptions=(TransientHTTPError, requests.RequestException))
def place_limit_order(symbol: str, side: str, price: float) -> dict:
    """
    Place un ordre LIMIT en 'size' (lots), conforme aux specs du contrat.
    - ajuste positionMode et marginMode avant l'ordre
    - arrondit price au tickSize
    """
    if not KUCOIN_API_KEY or not KUCOIN_API_SECRET or not KUCOIN_API_PASSPHRASE:
        LOGGER.error("KuCoin API credentials missing.")
        return {"ok": False, "error": "missing_api_credentials"}

    # 1) Contrat & arrondis
    meta = get_contract_info(symbol)
    lot_size = int(meta.get("lotSize", 1))
    multiplier = float(meta.get("multiplier", 1.0))
    tick = float(meta.get("tickSize", 0.01))

    adj_price = _round_price(float(price), tick)

    # 2) Sizing en lots via budget notionnel = marge * levier
    budget = float(MARGIN_USDT) * float(LEVERAGE)
    size_lots = _compute_lots_for_value(adj_price, multiplier, lot_size, budget)

    # 3) Modes (best effort)
    _ensure_position_mode(DEFAULT_POSITION_MODE)
    _ensure_margin_mode(symbol, DEFAULT_MARGIN_MODE)

    # 4) Envoi ordre LIMIT
    ts = int(time.time() * 1000)
    client_oid = str(uuid.uuid4())
    body = {
        "clientOid": client_oid,
        "symbol": symbol,
        "side": side.lower(),          # buy / sell
        "type": "limit",
        "price": f"{adj_price:.8f}",
        "size": str(int(size_lots)),   # <-- taille en L0TS (entier, multiple de lotSize)
        "leverage": str(int(LEVERAGE)),
        "timeInForce": "GTC",
        "postOnly": True,
        # "reduceOnly": False,
    }

    sig, pph = _sign(ts, "POST", ORDERS_EP, body)
    headers = _headers(ts, sig, pph)
    resp = requests.post(BASE + ORDERS_EP, headers=headers, json=body, timeout=12)

    if _needs_retry(resp.status_code):
        raise TransientHTTPError(f"KuCoin 5xx {resp.status_code}: {resp.text}")

    data = _safe_json(resp)
    if resp.status_code != 200:
        # Log explicite des messages 330005/330011/100001
        LOGGER.error("KuCoin order error %s %s -> %s", resp.status_code, symbol, data)
        return {"ok": False, "status": resp.status_code, "body": data, "clientOid": client_oid}

    return {"ok": True, "data": data, "clientOid": client_oid, "price": adj_price, "size_lots": size_lots}
