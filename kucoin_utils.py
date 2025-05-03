import httpx
import pandas as pd

async def get_kucoin_perps():
    url = "https://api.kucoin.com/api/v1/contracts/active"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        data = r.json()
        return [x["symbol"] for x in data["data"] if x["symbol"].endswith("USDTM")]

async def fetch_klines(symbol, interval="4hour", limit=100):
    url = f"https://api.kucoin.com/api/v1/kline/query?symbol={symbol}&granularity=14400"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        data = r.json().get("data", [])
        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit='s')
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        return df
