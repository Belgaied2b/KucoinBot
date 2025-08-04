import os
from kucoin.client import Trade

# Chargement des clés API KuCoin depuis Railway
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")

# Paramètres du trade
TRADE_AMOUNT = 20       # En USDT
TRADE_LEVERAGE = 3      # Levier 3x

# Initialisation du client KuCoin Futures
trade_client = Trade(
    key=KUCOIN_API_KEY,
    secret=KUCOIN_API_SECRET,
    passphrase=KUCOIN_API_PASSPHRASE,
    is_sandbox=False  # True = testnet KuCoin, False = live
)

def execute_order(symbol: str, direction: str):
    """
    Place un ordre market LONG ou SHORT sur KuCoin Futures PERP
    """
    try:
        kucoin_symbol = symbol.replace("USDT", "") + "USDTM"
        side = "buy" if direction == "long" else "sell"

        response = trade_client.create_market_order(
            symbol=kucoin_symbol,
            side=side,
            size=TRADE_AMOUNT,
            leverage=TRADE_LEVERAGE,
            type="market"
        )

        print(f"✅ Ordre {side.upper()} exécuté sur {kucoin_symbol} - {TRADE_AMOUNT}$ x{TRADE_LEVERAGE}")
        return response

    except Exception as e:
        print(f"❌ Échec de l’ordre sur {symbol} : {e}")
        return None
