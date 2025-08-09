import httpx, time, pandas as pd
from typing import Dict, Any

BASE = "https://api-futures.kucoin.com"

def fetch_klines(symbol: str, granularity: int = 1, limit: int = 300):
    end=int(time.time()); start=end - limit*60
    url=f"{BASE}/api/v1/kline/query?symbol={symbol}&granularity={granularity}&from={start}&to={end}"
    r=httpx.get(url, timeout=10.0); r.raise_for_status()
    arr=r.json().get("data",[])
    rows=[]
    for it in arr:
        ts=it[0]*1000; o=float(it[1]); h=float(it[3]); l=float(it[4]); c=float(it[2]); v=float(it[5])
        rows.append({"time":ts,"open":o,"high":h,"low":l,"close":c,"volume":v})
    return pd.DataFrame(rows).sort_values("time")

def fetch_symbol_meta() -> Dict[str, Dict[str, Any]]:
    url = f"{BASE}/api/v1/contracts/active"
    r = httpx.get(url, timeout=10.0); r.raise_for_status()
    meta={}
    for it in r.json().get("data", []):
        sym = it.get("symbol","")
        if not sym: 
            continue
        base_sym = sym.replace("USDTM","USDT")
        tick = float(it.get("tickSize", it.get("priceIncrement", 0.1)) or 0.1)
        prec = int(it.get("pricePrecision", 1))
        meta[base_sym] = {"tickSize": tick, "pricePrecision": prec}
    return meta

def round_price(symbol: str, price: float, meta: Dict[str, Dict[str, Any]], default_tick: float = 0.1) -> float:
    m = meta.get(symbol, {})
    tick = float(m.get("tickSize", default_tick))
    prec = int(m.get("pricePrecision", max(0, len(str(tick).split(".")[-1]))))
    rounded = round(round(price / tick) * tick, prec)
    return rounded
