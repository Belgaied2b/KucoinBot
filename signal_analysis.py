import pandas as pd
from indicators import (
    calculate_indicators, is_price_in_ote_zone,
    find_fvg_zone, is_bullish_breakout,
    is_bearish_breakout, is_volume_strong,
    is_bullish_candle, is_bearish_candle,
    is_above_ma200, is_below_ma200,
    calculate_sl_tp_levels
)
from telegram import Bot
import os

# Initialisation du bot Telegram
bot = Bot(token=os.getenv("TOKEN"))
chat_id = int(os.getenv("CHAT_ID"))

# Valeurs fixes
TRADE_AMOUNT = 20
TRADE_LEVERAGE = 3

def analyze_signal(df, symbol, direction):
    df = calculate_indicators(df)

    if len(df) < 50 or 'close' not in df.columns:
        return None

    latest = df.iloc[-1]
    entry_price = latest['close']

    # MA200
    if direction == "long" and not is_above_ma200(df):
        return None
    if direction == "short" and not is_below_ma200(df):
        return None

    # Breakout de structure (BOS)
    if direction == "long" and not is_bullish_breakout(df):
        return None
    if direction == "short" and not is_bearish_breakout(df):
        return None

    # Bougie de confirmation
    if direction == "long" and not is_bullish_candle(df):
        return None
    if direction == "short" and not is_bearish_candle(df):
        return None

    # Volume fort
    if not is_volume_strong(df):
        return None

    # Zones OTE + FVG
    ote_zone = is_price_in_ote_zone(df, direction)
    fvg_zone = find_fvg_zone(df, direction)

    if not ote_zone or not fvg_zone:
        return None

    # Vérifie que le prix actuel est DANS les 2 zones
    if not (ote_zone[0] <= entry_price <= ote_zone[1]) or not (fvg_zone[0] <= entry_price <= fvg_zone[1]):
        return None

    # SL / TP dynamiques
    sl, tp = calculate_sl_tp_levels(df, direction)

    # Envoie du signal Telegram
    message = f"""
📈 Signal CONFIRMÉ - {symbol}
Direction : {direction.upper()}
Prix actuel : {entry_price:.4f}

🎯 Entrée : dans OTE + FVG ✅
🛡 SL : {sl:.4f}
🎯 TP : {tp:.4f}
💰 Taille : {TRADE_AMOUNT} USDT
📊 Levier : x{TRADE_LEVERAGE}
"""
    bot.send_message(chat_id=chat_id, text=message)

    # Renvoie les infos pour passage d'ordre
    return {
        "symbol": symbol,
        "entry": entry_price,
        "sl": sl,
        "tp": tp,
        "side": direction,
        "amount": TRADE_AMOUNT,
        "leverage": TRADE_LEVERAGE
    }
