import httpx
import pandas as pd
import time
import logging

logger = logging.getLogger(__name__)
BASE_URL = "https://api-futures.kucoin.com"

def get_kucoin_perps():
    """
    Récupère la liste des symboles PERP USDT via l'endpoint public /contracts/active.
    """
    url = f"{BASE_URL}/api/v1/contracts/active"
    resp = httpx.get(url)
    resp.raise_for_status()
    data = resp.json()
    # Vérifie le code de succès
    if data.get("code") not in ("200000", None):
        logger.error(f"❌ get_kucoin_perps → code={data['code']} msg={data.get('msg')}")
        return []
    contracts = data.get("data", [])
    perps = [c["symbol"] for c in contracts if c.get("quoteCurrency") == "USDT"]
    logger.info(f"📊 {len(perps)} PERP USDT récupérés")
    return perps

def fetch_klines(symbol, interval="4hour", limit=150):
    """
    Récupère les bougies 4H (granularity=240 minutes) pour un symbol donné.
    """
    # mapping interval → minutes (API Futures attend des minutes)
    granularity_map = {"4hour": 240}
    minutes = granularity_map[interval]

    url = f"{BASE_URL}/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": minutes, "limit": limit}

    resp = httpx.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    # L’API répond toujours code 200000 pour OK
    if data.get("code") != "200000":
        raise ValueError(f"{symbol} → code={data.get('code')} msg={data.get('msg')}")

    raw = data.get("data", [])
    if not raw:
        raise ValueError(f"{symbol} → pas de données 4H disponibles")

    # Construire le DataFrame
    df = pd.DataFrame(raw, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "turnover"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    # Pause pour respecter le rate-limit
    time.sleep(0.2)

    logger.info(f"✅ {symbol} : {len(df)} bougies 4H récupérées")
    return df
