from kucoin_futures.client import Market
import pandas as pd, time, logging

logger = logging.getLogger(__name__)
client = Market(url="https://api-futures.kucoin.com")
logger.info("✅ API KuCoin Futures initialisée")

# mapping interval → minutes
_INTERVALS = {"4hour": 240}

def fetch_klines(symbol, interval="4hour", limit=150):
    minutes = _INTERVALS[interval]
    try:
        logger.info(f"📥 Requête 4H → {symbol} (granularity={minutes})")
        # APPEL CORRECT : pas de 'params='
        raw = client.get_kline_data(symbol=symbol, granularity=minutes)
        if not raw:
            raise ValueError("Aucune bougie reçue")
        # raw est une liste de listes : [ [ts, open, high, low, close, vol, turnover], … ]
        df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume","turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        time.sleep(0.2)
        logger.info(f"✅ Données 4H récupérées pour {symbol} ({len(df)} bougies)")
        return df
    except Exception as e:
        logger.error(f"❌ {symbol} → Erreur récupération 4H : {e}")
        raise
