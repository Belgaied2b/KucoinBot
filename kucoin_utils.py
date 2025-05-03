from kucoin_futures.client import Market
import pandas as pd
import time
import logging

logger = logging.getLogger(__name__)

# Initialisation explicite de l'API Futures
client = Market(url='https://api-futures.kucoin.com')
logger.info("✅ API KuCoin Futures initialisée avec succès (kucoin-futures-python)")

def get_kucoin_perps():
    try:
        contracts = client.get_contracts_list()
        logger.info(f"📊 {len(contracts)} contrats PERP récupérés depuis l’API.")
        return [c['symbol'] for c in contracts if c['quoteCurrency'] == 'USDT']
    except Exception as e:
        logger.error(f"❌ Erreur lors de la récupération des contrats PERP : {e}")
        return []

def fetch_klines(symbol, interval="4hour", limit=150):
    seconds = {
        "4hour": 14400
    }[interval]

    try:
        logger.info(f"📥 Requête 4H → {symbol} (granularity={seconds})")
        raw = client.get_kline_data(symbol=symbol, granularity=seconds)
        if not raw:
            raise ValueError("Réponse vide de l’API.")
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        time.sleep(0.2)
        logger.info(f"✅ Données 4H récupérées pour {symbol} ({len(df)} bougies)")
        return df
    except Exception as e:
        logger.error(f"❌ {symbol} → Erreur récupération 4H : {e}")
        raise
