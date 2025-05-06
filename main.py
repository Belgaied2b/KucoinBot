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

# Config
EXCHANGES = ['binanceusdm', 'bybit']  # ccxt.pro class names
SYMBOLS   = ['BTC/USDT', 'ETH/USDT']
TF_LOW    = '1m'
TF_HIGH   = '15m'
RISK_PCT  = 0.01

alert_mgr = AlertManager(cooldown=300)

async def handle_update(event_type, ex, symbol, data):
    # 1) Reconstruction du DF low TF / high TF
    #    (on agrège multi-exchange puis on resample pour high TF)
    df_low = ExchangeAggregator(ds).get_ohlcv_df(symbol)
    if df_low is None or len(df_low) < 50:
        return
    df_low = df_low.copy()
    df_high = df_low.resample(TF_HIGH).agg({
        'open':'first','high':'max','low':'min','close':'last','volume':'sum'
    }).dropna()

    # 2) Détection signal long et short low TF
    for side in ('long','short'):
        res_low = analyze_market(symbol, df_low, side=side)
        if not res_low:
            continue

        # 3) Confirmation sur high TF
        if not confirm_multi_tf(symbol, df_low, df_high, side):
            continue

        # 4) Filtre orderbook
        obs = ExchangeAggregator(ds).get_orderbook(symbol)
        imb = detect_imbalance(obs)
        if (side=='long' and imb!='buy') or (side=='short' and imb!='sell'):
            continue

        # 5) Size dynamique
        bal = get_account_balance(symbol)
        size = calculate_position_size(bal, RISK_PCT, res_low['stop_loss'] - res_low['entry_price'] if side=='long' else res_low['entry_price']-res_low['stop_loss'])

        # 6) Anti-spam
        key_base = (symbol, side, round(res_low['entry_price'], 4))
        for alert_type in ('anticipation','zone','signal'):
            key = key_base + (alert_type,)
            if not alert_mgr.can_send(key):
                continue

            # 7) Génération du chart en Base64
            img_b64 = generate_signal_chart(df_low, res_low['entry_min'], res_low['entry_max'], symbol, TF_LOW)
            caption = f"🔔 {alert_type.upper()} {side.upper()} {symbol}\nEntry zone: {res_low['entry_min']:.4f}-{res_low['entry_max']:.4f}\nPrix actuel: {df_low['close'].iloc[-1]:.4f}\nSize: {size:.4f}"
            send_telegram(caption, image_b64=img_b64)

async def main():
    global ds
    ds = DataStream(EXCHANGES, SYMBOLS, TF_LOW)
    await ds.start(handle_update)

if __name__ == "__main__":
    asyncio.run(main())
