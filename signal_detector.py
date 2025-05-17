import ccxt
import pandas as pd
import datetime
from config import TELEGRAM_BOT, TELEGRAM_CHAT_ID
from chart_generator import generate_chart
from utils import get_ohlcv, is_bos_valid, is_btc_favorable, detect_ote_fvg_zone, calculate_dynamic_sl_tp, already_sent, save_sent_signal

exchange = ccxt.kucoin()
markets = exchange.load_markets()
symbols = [s for s in markets if "USDT:USDT" in s and "PERP" in s]

async def auto_scan_and_send_signals():
    for symbol in symbols:
        try:
            df = get_ohlcv(symbol, timeframe='1h', limit=200)
            if df is None or len(df) < 100:
                continue

            last_close = df['close'].iloc[-1]
            volume = df['volume'].iloc[-1]
            prev_volume = df['volume'].iloc[-2]

            # Vérifier BOS, volume, BTC favorable
            if not is_bos_valid(df):
                continue
            if volume < prev_volume:
                continue
            if not is_btc_favorable():
                continue

            # Détection zone OTE + FVG
            ote_zone, fvg_zone = detect_ote_fvg_zone(df)
            if ote_zone is None or fvg_zone is None:
                continue

            # Vérifie clôture au-dessus OTE+FVG (confirmation)
            close_confirmed = last_close > max(ote_zone[0], fvg_zone[0])
            entry = round((ote_zone[0] + fvg_zone[0]) / 2, 6)
            sl, tp = calculate_dynamic_sl_tp(df, entry)

            direction = "LONG"
            signal_type = "CONFIRMÉ" if close_confirmed else "ANTICIPÉ"
            unique_id = f"{symbol}-{signal_type}"

            if already_sent(unique_id):
                continue

            # Génère graphique
            chart_path = generate_chart(df, symbol, ote_zone, fvg_zone, entry, sl, tp, direction)

            # Message Telegram
            message = f"""
{symbol} - Signal {signal_type} ({direction})

🔵 Entrée idéale : {entry}
🛑 SL : {sl}
🎯 TP : {tp}
📈 Signal {'confirmé' if signal_type == 'CONFIRMÉ' else 'anticipé'} avec conditions {'complètes ✅' if signal_type == 'CONFIRMÉ' else 'partielles ⏳'}
"""

            await TELEGRAM_BOT.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=open(chart_path, 'rb'), caption=message)
            save_sent_signal(unique_id)

        except Exception as e:
            print(f"[Erreur {symbol}] {e}")
