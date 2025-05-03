import httpx
import pandas as pd
import time
import logging

logger = logging.getLogger(__name__)
BASE_URL = "https://api-futures.kucoin.com"

def get_kucoin_perps():
    url = f"{BASE_URL}/api/v1/contracts/active"
    resp = httpx.get(url)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in ("200000", None):
        logger.error(f"❌ get_kucoin_perps → code={data['code']} msg={data.get('msg')}")
        return []
    perps = [c["symbol"] for c in data.get("data", []) if c.get("quoteCurrency")=="USDT"]
    logger.info(f"📊 {len(perps)} PERP USDT récupérés")
    return perps

def fetch_klines(symbol, interval="4hour", limit=150):
    granularity_map = {"4hour": 240}
    minutes = granularity_map[interval]
    url = f"{BASE_URL}/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": minutes, "limit": limit}

    resp = httpx.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "200000":
        raise ValueError(f"{symbol} → code={data.get('code')} msg={data.get('msg')}")
    raw = data.get("data", [])
    if not raw:
        raise ValueError(f"{symbol} → pas de données 4H disponibles")

    # Détermination dynamique des colonnes
    first = raw[0]
    if len(first) == 7:
        cols = ["timestamp","open","high","low","close","volume","turnover"]
    elif len(first) == 6:
        cols = ["timestamp","open","high","low","close","volume"]
    else:
        raise ValueError(f"{symbol} → format inattendu: {len(first)} colonnes")

    df = pd.DataFrame(raw, columns=cols)
    # <<< CORRECTION ICI : timestamp en millisecondes >>>
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    time.sleep(0.2)
    logger.info(f"✅ {symbol} : {len(df)} bougies 4H récupérées")
    return df
