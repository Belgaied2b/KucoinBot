from kucoin_futures.client import Market
import pandas as pd
import time, logging

logger = logging.getLogger(__name__)
client = Market(url='https://api-futures.kucoin.com')
logger.info("✅ API KuCoin Futures initialisée")

# Mapping interval → minutes
_INTERVAL_MINUTES = {
    "4hour": 240
}

def get_kucoin_perps():
    contracts = client.get_contracts_list()
    return [c['symbol'] for c in contracts if c['quoteCurrency'] == 'USDT']

def fetch_klines(symbol, interval="4hour", limit=150):
    minutes = _INTERVAL_MINUTES[interval]

    try:
        logger.info(f"📥 Requête 4H → {symbol} (granularity={minutes} minutes)")
        raw = client.get_kline_data(   # ou client._request si vous préférez l’appel brut
            "/api/v1/kline/query",
            params={"symbol": symbol, "granularity": minutes, "limit": limit}
        )
        # si vous utilisez client._request :
        # raw = client._request("GET", "/api/v1/kline/query", params={"symbol": symbol, "granularity": minutes, "limit": limit})

        if raw.get("code") != "200000" or not raw.get("data"):
            raise ValueError(f"{symbol} → pas de données 4H (code {raw.get('code')})")

        df = pd.DataFrame(raw["data"], columns=[
            "timestamp", "open", "high", "low", "close", "volume"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        time.sleep(0.2)
        logger.info(f"✅ Données 4H récupérées pour {symbol} ({len(df)} bougies)")
        return df

    except Exception as e:
        logger.error(f"❌ {symbol} → Erreur récupération 4H : {e}")
        raise
