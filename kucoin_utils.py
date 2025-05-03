from kucoin_futures.client import Market
import pandas as pd
import time
import logging

logger = logging.getLogger(__name__)

# Initialise le client Futures
client = Market(url='https://api-futures.kucoin.com')
logger.info("✅ API KuCoin Futures initialisée (kucoin-futures-python)")

def get_kucoin_perps():
    """
    Récupère la liste des symboles PERP en USDT.
    """
    try:
        contracts = client.get_contracts_list()
        usdt = [c['symbol'] for c in contracts if c.get('quoteCurrency') == 'USDT']
        logger.info(f"📊 {len(usdt)} PERP en USDT trouvés.")
        return usdt
    except Exception as e:
        logger.error(f"❌ Erreur récupération PERP : {e}")
        return []

def fetch_klines(symbol, interval="4hour", limit=150):
    """
    Récupère les bougies en 4 H (granularity=240 minutes) pour le symbol donné.
    """
    # mapping interval -> minutes attendu par l'API
    _INTERVALS = {"4hour": 240}
    minutes = _INTERVALS.get(interval, 240)

    try:
        logger.info(f"📥 Requête 4H → {symbol} (granularity={minutes} min, limit={limit})")
        # Appel au wrapper officiel
        raw = client.get_kline_data(symbol=symbol, granularity=minutes)
        if not raw:
            raise ValueError("Aucune donnée retournée par l'API")
        # raw est une liste de listes [ts, open, high, low, close, vol, turnover, ...]
        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        # Conversion du timestamp Unix (en secondes) en datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        # Pause pour ne pas trop solliciter l'API
        time.sleep(0.2)
        logger.info(f"✅ {symbol} : {len(df)} bougies 4H récupérées")
        return df
    except Exception as e:
        logger.error(f"❌ {symbol} → Erreur récupération 4H : {e}")
        # remonte l'erreur pour permettre à scanner.py de la logger aussi
        raise
