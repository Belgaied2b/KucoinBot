# main.py

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

# === CONFIGURATION ===
# On ne charge plus que KuCoin Futures
EXCHANGES = ['kucoinfutures']    # ccxt.pro client name pour KuCoin Futures
SYMBOLS   = ['BTC/USDT', 'ETH/USDT']
TF_LOW    = '1m'
TF_HIGH   = '15m'
RISK_PCT  = 0.01

alert_mgr = AlertManager(cooldown=300)

async def handle_update(event_type, ex, symbol, data):
    # 1) Reconstruction des DFs bas et haut TF
    df_low = ExchangeAggregator(ds).get_ohlcv_df(symbol)
    if df_low is None or len(df_low) < 50:
        return
    df_low = df_low.copy()
    df_high = df_low.resample(TF_HIGH).agg({
        'open':'first','high':'max','low':'min','close':'last','volume':'sum'
    }).dropna()

    # 2) Scan long/short sur le low TF
    for side in ('long','short'):
        res_low = analyze_market(symbol, df_low, side=side)
        if not res_low:
            continue

        # 3) Confirmation multi-timeframe
        if not confirm_multi_tf(symbol, df_low, df_high, side):
            continue

        # 4) Filtre orderbook (imbalance)
        obs = ExchangeAggregator(ds).get_orderbook(symbol)
        imb = detect_imbalance(obs)
        if (side=='long' and imb!='buy') or (side=='short' and imb!='sell'):
            continue

        # 5) Taille de position dynamique
        bal = get_account_balance(symbol)
        # pour long, sl-entry ; pour short, entry-sl
        risk_dist = (res_low['entry_price'] - res_low['stop_loss']) if side=='long' else (res_low['stop_loss'] - res_low['entry_price'])
        size = calculate_position_size(bal, RISK_PCT, risk_dist)

        # 6) Envoi des alertes (anticipation, zone, signal) avec anti-spam
        key_base = (symbol, side, round(res_low['entry_price'], 4))
        for alert_type in ('anticipation','zone','signal'):
            key = key_base + (alert_type,)
            if not alert_mgr.can_send(key):
                continue

            # Génération du graphique
            img_b64 = generate_signal_chart(df_low, res_low['entry_min'], res_low['entry_max'], symbol, TF_LOW)
            caption = (
                f"🔔 {alert_type.upper()} {side.upper()} {symbol}\n"
                f"Zone d'entrée : {res_low['entry_min']:.4f} – {res_low['entry_max']:.4f}\n"
                f"Prix actuel   : {df_low['close'].iloc[-1]:.4f}\n"
                f"Taille        : {size:.4f}"
            )
            send_telegram(caption, image_b64=img_b64)

async def main():
    global ds
    # On instancie le DataStream sur KuCoin Futures uniquement
    ds = DataStream(EXCHANGES, SYMBOLS, TF_LOW)
    await ds.start(handle_update)

if __name__ == "__main__":
    asyncio.run(main())
