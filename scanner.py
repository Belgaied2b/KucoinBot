import os
import json
import time
from kucoin.client import Trade
from kucoin_futures.client import Market
from signal_analysis import analyze_signal
from kucoin_utils import get_symbols_data
import telegram

# Constantes fixes
TRADE_AMOUNT = 20  # USDT
TRADE_LEVERAGE = 3

# API KuCoin (depuis Railway)
api_key = os.getenv("KUCOIN_API_KEY")
api_secret = os.getenv("KUCOIN_API_SECRET")
api_passphrase = os.getenv("KUCOIN_API_PASSPHRASE")

# Telegram
TELEGRAM_TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# KuCoin clients
market_client = Market()
trade_client = Trade(key=api_key, secret=api_secret, passphrase=api_passphrase, is_sandbox=False)

# Gestion des doublons
sent_signals_path = "sent_signals.json"
if not os.path.exists(sent_signals_path):
    with open(sent_signals_path, "w") as f:
        json.dump([], f)

def load_sent_signals():
    with open(sent_signals_path, "r") as f:
        return json.load(f)

def save_sent_signal(symbol, direction):
    signals = load_sent_signals()
    signals.append(f"{symbol}_{direction}")
    with open(sent_signals_path, "w") as f:
        json.dump(signals, f)

def format_signal_message(signal):
    return f"""🚨 *SIGNAL CONFIRMÉ {signal['direction'].upper()}* 🚨

*Pair:* `{signal['symbol']}`
*Entrée:* `{signal['entry']}`
*SL:* `{signal['sl']}`
*TP:* `{signal['tp']}`
*Zone OTE:* `{signal['ote_zone']}`
*Zone FVG:* `{signal['fvg_zone']}`

*Levier:* {TRADE_LEVERAGE}x
💰 _Ordre exécuté automatiquement_

#crypto #bot #kucoin #trade
"""

def place_order(symbol, direction, entry, sl, tp):
    try:
        side = "buy" if direction == "long" else "sell"
        kucoin_symbol = symbol.replace("USDT", "") + "USDTM"

        trade_client.create_market_order(
            symbol=kucoin_symbol,
            side=side,
            leverage=TRADE_LEVERAGE,
            size=TRADE_AMOUNT,
            type="market"
        )
        print(f"✔️ Order placed on {symbol} ({direction})")
    except Exception as e:
        print(f"❌ Order failed on {symbol}: {e}")

def scan_and_trade():
    print("🔍 Scan démarré...")
    data = get_symbols_data()
    sent = load_sent_signals()

    for symbol, df_dict in data.items():
        for direction in ["long", "short"]:
            tag = f"{symbol}_{direction}"
            if tag in sent:
                continue

            signal = analyze_signal(df_dict, symbol, direction)
            if signal:
                print(f"✅ Signal confirmé : {tag}")
                save_sent_signal(symbol, direction)
                msg = format_signal_message(signal)
                bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=telegram.ParseMode.MARKDOWN)

                # Execute trade
                place_order(symbol, direction, signal['entry'], signal['sl'], signal['tp'])
            else:
                print(f"❌ Aucun signal pour {tag}")

if __name__ == "__main__":
    while True:
        scan_and_trade()
        time.sleep(600)  # scan toutes les 10 minutes
