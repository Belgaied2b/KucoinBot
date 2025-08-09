import httpx, time, pandas as pd
from typing import Dict, Any, List

BASE = "https://api-futures.kucoin.com"

def fetch_klines(symbol: str, granularity: int = 1, limit: int = 300) -> pd.DataFrame:
    """
    OHLCV KuCoin Futures.
    Retourne un DataFrame trié par time (ms) avec colonnes: time, open, high, low, close, volume.
    """
    end = int(time.time())
    start = end - limit * 60
    url = f"{BASE}/api/v1/kline/query?symbol={symbol}&granularity={granularity}&from={start}&to={end}"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    arr = r.json().get("data", []) or []
    rows: list[dict] = []
    # KuCoin renvoie: [timestamp(s), open, close, high, low, volume, turnover]
    for it in arr:
        try:
            ts = int(it[0]) * 1000
            o = float(it[1]); c = float(it[2]); h = float(it[3]); l = float(it[4]); v = float(it[5])
            rows.append({"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})
        except Exception:
            continue
    if not rows:
        return pd.DataFrame(columns=["time","open","high","low","close","volume"])
    df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    return df

def fetch_symbol_meta() -> Dict[str, Dict[str, Any]]:
    """
    Récupère tickSize / pricePrecision pour les contrats actifs KuCoin Futures.
    Normalise en symbole 'BTCUSDT' (remplace 'USDTM' -> 'USDT').
    """
    url = f"{BASE}/api/v1/contracts/active"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    meta: Dict[str, Dict[str, Any]] = {}
    for it in r.json().get("data", []) or []:
        sym = (it.get("symbol") or "").strip()
        if not sym:
            continue
        base_sym = sym.replace("USDTM", "USDT")
        tick = float(it.get("tickSize") or it.get("priceIncrement") or 0.1)
        prec = int(it.get("pricePrecision") or max(0, len(str(tick).split(".")[-1])))
        meta[base_sym] = {"tickSize": tick, "pricePrecision": prec}
    return meta

def round_price(symbol: str, price: float, meta: Dict[str, Dict[str, Any]], default_tick: float = 0.1) -> float:
    """
    Arrondit le prix au multiple de tick du symbole, avec la bonne précision.
    """
    m = meta.get(symbol, {})
    tick = float(m.get("tickSize", default_tick))
    prec = int(m.get("pricePrecision", max(0, len(str(tick).split(".")[-1]))))
    # arrondi au multiple de tick
    stepped = round(price / tick) * tick
    return round(stepped, prec)

# -----------------------------
# Helpers découverte de symboles
# -----------------------------

def kucoin_active_usdt_symbols() -> List[str]:
    """
    Liste des symboles USDT actifs côté KuCoin Futures, normalisés en 'XXXUSDT'.
    """
    url = f"{BASE}/api/v1/contracts/active"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    out: List[str] = []
    for it in r.json().get("data", []) or []:
        sym = (it.get("symbol") or "").strip()
        if not sym:
            continue
        if sym.endswith("USDTM"):
            sym = sym.replace("USDTM", "USDT")
        if sym.endswith("USDT"):
            out.append(sym)
    return sorted(set(out))

def binance_usdt_perp_symbols() -> List[str]:
    """
    Liste des PERPETUAL USDT-M côté Binance Futures (pour croiser avec KuCoin).
    """
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    out: List[str] = []
    for s in r.json().get("symbols", []) or []:
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ):
            out.append(s.get("symbol", ""))
    return sorted(set(out))

def common_usdt_symbols(limit: int = 50, exclude_csv: str = "") -> List[str]:
    """
    Renvoie les symboles USDT communs KuCoin/Binance (stables) pour scanner.
    - limit: nombre max
    - exclude_csv: chaîne "ABCUSDT,XYZUSDT" à exclure
    """
    try:
        k = set(kucoin_active_usdt_symbols())
        b = set(binance_usdt_perp_symbols())
        common = [s for s in sorted(k & b)]
    except Exception:
        # fallback si Binance indispo: KuCoin uniquement
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
