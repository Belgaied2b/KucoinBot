import time
from typing import Dict, Any, List, Optional

import httpx
import pandas as pd

BASE = "https://api-futures.kucoin.com"


def _client(timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": "scanner/1.0"},
    )


def fetch_klines(symbol: str, granularity: int = 1, limit: int = 300) -> pd.DataFrame:
    """
    OHLCV KuCoin Futures.
    - symbol: nom exact du contrat (ex: BTCUSDTM)
    - granularity: minutes (1,5,15,...)
    - limit: ~nb de bougies souhaitées (fenêtre temps calculée)
    Retourne un DataFrame trié par time (ms) avec colonnes:
    time, open, high, low, close, volume
    """
    now_ms = int(time.time() * 1000)
    # fenêtre temps = limit * granularity minutes
    window_ms = limit * granularity * 60_000
    start_ms = max(0, now_ms - window_ms)

    params = {
        "symbol": symbol,
        "granularity": granularity,
        "from": start_ms,
        "to": now_ms,
    }

    rows: List[Dict[str, Any]] = []
    try:
        with _client() as c:
            r = c.get(f"{BASE}/api/v1/kline/query", params=params)
            r.raise_for_status()
            arr = r.json().get("data", []) or []
    except Exception:
        arr = []

    # KuCoin Futures renvoie: [time(ms|s), open, high, low, close, volume, ...]
    for it in arr:
        try:
            ts_raw = int(it[0])
            # tolérance: si <= 10^10, on considère que c'est en secondes -> converti en ms
            ts = ts_raw * 1000 if ts_raw < 10_000_000_000 else ts_raw
            o = float(it[1])
            h = float(it[2])
            l = float(it[3])
            c = float(it[4])
            v = float(it[5])
            rows.append({"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})
        except Exception:
            continue

    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def _infer_precision_from_tick(tick: float) -> int:
    s = f"{tick:.12f}".rstrip("0").rstrip(".")
    if "." in s:
        return max(0, len(s.split(".")[1]))
    return 0


def fetch_symbol_meta() -> Dict[str, Dict[str, Any]]:
    """
    Métadonnées contrats actifs KuCoin Futures (USDTM).
    - Clé du dict: symbole affichage (ex: BTCUSDT)
    - Inclut 'symbol_api' (ex: BTCUSDTM) + tickSize + pricePrecision
    """
    url = f"{BASE}/api/v1/contracts/active"
    meta: Dict[str, Dict[str, Any]] = {}

    try:
        with _client() as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json().get("data", []) or []
    except Exception:
        data = []

    for it in data:
        try:
            sym_api = (it.get("symbol") or "").strip()  # ex: BTCUSDTM
            if not sym_api or not sym_api.endswith("USDTM"):
                continue

            display = sym_api.replace("USDTM", "USDT")

            # Certains champs peuvent être str -> cast float
            tick_raw: Optional[Any] = it.get("tickSize", it.get("priceIncrement", 0.1))
            tick = float(tick_raw) if tick_raw is not None else 0.1
            prec_raw: Optional[Any] = it.get("pricePrecision")
            if prec_raw is None:
                prec = _infer_precision_from_tick(tick)
            else:
                try:
                    prec = int(prec_raw)
                except Exception:
                    prec = _infer_precision_from_tick(tick)

            meta[display] = {
                "symbol_api": sym_api,
                "tickSize": tick,
                "pricePrecision": prec,
            }
        except Exception:
            continue

    return meta


def round_price(symbol: str, price: float, meta: Dict[str, Dict[str, Any]], default_tick: float = 0.1) -> float:
    """
    Arrondit le prix au multiple de tick du symbole affiché (ex: BTCUSDT),
    en utilisant tickSize/pricePrecision de meta.
    """
    m = meta.get(symbol, {})
    tick = float(m.get("tickSize", default_tick))
    prec = int(m.get("pricePrecision", _infer_precision_from_tick(tick)))
    # éviter division par zéro
    if tick <= 0:
        tick = default_tick
        prec = _infer_precision_from_tick(tick)
    stepped = round(price / tick) * tick
    return round(stepped, prec)


# -----------------------------
# Helpers découverte de symboles
# -----------------------------

def kucoin_active_usdt_symbols() -> List[str]:
    """
    Liste des symboles USDT actifs côté KuCoin Futures (version affichage 'XXXUSDT').
    """
    url = f"{BASE}/api/v1/contracts/active"
    out: List[str] = []
    try:
        with _client() as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json().get("data", []) or []
    except Exception:
        data = []

    for it in data:
        sym = (it.get("symbol") or "").strip()
        if sym.endswith("USDTM"):
            out.append(sym.replace("USDTM", "USDT"))

    # dédup + tri alphabétique
    return sorted(set(out))


def binance_usdt_perp_symbols() -> List[str]:
    """
    Liste des PERPETUAL USDT-M côté Binance Futures (pour croiser avec KuCoin).
    """
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    out: List[str] = []
    try:
        with _client() as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json().get("symbols", []) or []
    except Exception:
        data = []

    for s in data:
        try:
            if (
                s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"
            ):
                sym = s.get("symbol", "")
                if sym:
                    out.append(sym)
        except Exception:
            continue

    return sorted(set(out))


def common_usdt_symbols(limit: int = 0, exclude_csv: str = "") -> List[str]:
    """
    Renvoie les symboles USDT communs KuCoin/Binance (version affichage).
    - limit: nombre max (0 = pas de limite)
    - exclude_csv: "ABCUSDT,XYZUSDT" à exclure
    """
    try:
        k = set(kucoin_active_usdt_symbols())
        b = set(binance_usdt_perp_symbols())
        common = [s for s in sorted(k & b)]
    except Exception:
        # fallback: au pire, uniquement KuCoin
        common = kucoin_active_usdt_symbols()

    if exclude_csv:
        ex = {x.strip().upper() for x in exclude_csv.split(",") if x.strip()}
        common = [s for s in common if s not in ex]

    if limit and limit > 0:
        common = common[:limit]
    return common


__all__ = [
    "fetch_klines",
    "fetch_symbol_meta",
    "round_price",
    "kucoin_active_usdt_symbols",
    "binance_usdt_perp_symbols",
    "common_usdt_symbols",
]
