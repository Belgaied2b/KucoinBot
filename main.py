import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)

import asyncio
import pandas as pd
from data_stream import DataStream
from multi_exchange import ExchangeAggregator
from multi_tf import confirm_multi_tf
from orderbook_utils import detect_imbalance
from signal_analysis import analyze_market
from risk_manager import calculate_position_size
from alert_manager import AlertManager
from chart_generator import generate_signal_chart
from kucoin_utils import send_telegram, get_account_balance

logger = logging.getLogger(__name__)

# === CONFIGURATION ===
EXCHANGES = ['kucoinfutures']              # Uniquement KuCoin Futures
SYMBOLS   = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
TF_LOW    = '1m'
TF_HIGH   = '15m'
RISK_PCT  = 0.01

alert_mgr = AlertManager(cooldown=300)

async def handle_update(event_type, ex, symbol, data):
    # Reconstruction des DataFrames bas (1m) et haut (15m)
    df_low = ExchangeAggregator(ds).get_ohlcv_df(symbol)
    if df_low is None or len(df_low) < 50:
        return
    df_high = (
        df_low
        .resample(TF_HIGH)
        .agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
        .dropna()
    )

    for side in ('long', 'short'):
        res_low = analyze_market(symbol, df_low, side=side)
        if not res_low:
            continue

        # Confirmation multi-timeframe
        if not confirm_multi_tf(symbol, df_low, df_high, side):
            continue

        # Filtre orderbook
        obs = ExchangeAggregator(ds).get_orderbook(symbol)
        imb = detect_imbalance(obs)
        if (side == 'long' and imb != 'buy') or (side == 'short' and imb != 'sell'):
            continue

        # Position sizing dynamique
        bal = get_account_balance(symbol)
        risk_dist = (
            res_low['entry_price'] - res_low['stop_loss']
            if side == 'long'
            else res_low['stop_loss'] - res_low['entry_price']
        )
        size = calculate_position_size(bal, RISK_PCT, risk_dist)

        # Envoi des alertes avec anti-spam
        key_base = (symbol, side, round(res_low['entry_price'], 4))
        for alert_type in ('anticipation', 'zone', 'signal'):
            key = key_base + (alert_type,)
            if not alert_mgr.can_send(key):
                continue

            img_b64 = generate_signal_chart(
                df_low,
                res_low['entry_min'],
                res_low['entry_max'],
                symbol,
                TF_LOW
            )
            caption = (
                f"🔔 {alert_type.upper()} {side.upper()} {symbol}\n"
                f"Zone d'entrée : {res_low['entry_min']:.4f} – {res_low['entry_max']:.4f}\n"
                f"Prix actuel   : {df_low['close'].iloc[-1]:.4f}\n"
                f"Taille        : {size:.4f}"
            )
            send_telegram(caption, image_b64=img_b64)

async def main():
    global ds
    logger.info("🚀 Démarrage de l’application – initialisation du DataStream")
    ds = DataStream(EXCHANGES, SYMBOLS, TF_LOW)
    try:
        await ds.start(handle_update)
    finally:
        # Fermer proprement chaque instance ccxt.pro
        for ex in ds.exchanges.values():
            await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
