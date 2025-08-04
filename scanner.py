import os
from kucoin_futures.client import Market
from kucoin.client import Client
from kucoin_futures.client import Trade
from kucoin_futures.client import User
from signal_analysis import analyze_signal
from kucoin_utils import get_symbols_data
import time

# Clés API KuCoin depuis Railway
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")

client = Client(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
market = Market()
trade = Trade(key=KUCOIN_API_KEY, secret=KUCOIN_API_SECRET, passphrase=KUCOIN_API_PASSPHRASE)
user = User(key=KUCOIN_API_KEY, secret=KUCOIN_API_SECRET, passphrase=KUCOIN_API_PASSPHRASE)

def execute_trade(signal):
    try:
        side = 'buy' if signal['side'] == 'long' else 'sell'
        trade.create_market_order(
            symbol=signal['symbol'],
            side=side,
            leverage=signal['leverage'],
            size=signal['amount'],
            clientOid=str(time.time())  # Identifiant unique
        )
        print(f"✅ Ordre exécuté sur {signal['symbol']} ({side})")
    except Exception as e:
        print(f"❌ Erreur exécution trade : {e}")

async def scan_and_send_signals():
    print("🔍 Scan en cours...")

    data = get_symbols_data()  # Tu peux ajouter un filtre ici

    for symbol, df in data.items():
        try:
            for direction in ['long', 'short']:
                signal = analyze_signal(df.copy(), symbol, direction)
                if signal:
                    execute_trade(signal)
        except Exception as e:
            print(f"⚠️ Erreur analyse {symbol} : {e}")
