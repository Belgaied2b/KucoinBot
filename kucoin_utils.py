# kucoin_utils.py

import httpx
import time

KUCOIN_BASE_URL = "https://api.kucoin.com"

async def get_kucoin_perps():
    url = f"{KUCOIN_BASE_URL}/api/v1/contracts/active"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=10)
            data = r.json()
            return [item["symbol"] for item in data["data"] if item["symbol"].endswith("USDTM")]
    except Exception as e:
        print(f"Erreur récupération PERP KuCoin : {e}")
        return []

async def fetch_klines(symbol, interval="4h", limit=100):
    url = f"{KUCOIN_BASE_URL}/api/v1/kline/query"
    params = {
        "symbol": symbol,
        "granularity": 14400,
        "limit": limit
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params, timeout=10)
            data = r.json()
            if "data" not in data or not data["data"]:
                return None
            # Format: [timestamp, open, close, high, low, volume, turnover]
            df = [
                {
                    "timestamp": int(candle[0]),
                    "open": float(candle[1]),
                    "close": float(candle[2]),
                    "high": float(candle[3]),
                    "low": float(candle[4]),
                    "volume": float(candle[5])
                }
                for candle in data["data"]
            ]
            return df[::-1]  # plus récents à la fin
    except Exception as e:
        print(f"Erreur fetch klines {symbol} : {e}")
        return None
