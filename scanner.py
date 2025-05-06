import time
from kucoin_utils import fetch_ohlcv, send_telegram
from signal_analysis import analyze_market

# Seuil d'anticipation (en %)
ANTICIPATION_THRESHOLD = 0.003  # 0,3%

def scan_symbols():
    symbols = [
        # Ajoute ici tes symboles favoris
        "BTC-USDT",
        "ETH-USDT",
        "XRP-USDT",
        # etc.
    ]
    for symbol in symbols:
        df = fetch_ohlcv(symbol)
        last_price = df["close"].iloc[-1]

        # ——— GESTION DES LONG ———
        result_long = analyze_market(symbol, df, side="long")
        if result_long:
            el_min = result_long["entry_min"]
            el_max = result_long["entry_max"]

            # 1) Anticipation LONG
            if el_min * (1 - ANTICIPATION_THRESHOLD) <= last_price < el_min:
                send_telegram(
                    f"⏳ Anticipation LONG {symbol}\n"
                    f"Le prix s'approche de la zone : {el_min:.4f} → {el_max:.4f}\n"
                    f"Prix actuel : {last_price:.4f}"
                )

            # 2) Zone LONG atteinte
            if el_min <= last_price <= el_max:
                send_telegram(
                    f"🚨 Zone de LONG atteinte {symbol}\n"
                    f"Entrée possible entre {el_min:.4f} et {el_max:.4f}\n"
                    f"Prix actuel : {last_price:.4f}"
                )

            # 3) Signal LONG final (avec SL/TP)
            send_telegram(
                f"🟢 LONG {symbol}\n"
                f"Entry : {result_long['entry_price']:.4f}\n"
                f"SL    : {result_long['stop_loss']:.4f}\n"
                f"TP1   : {result_long['tp1']:.4f}\n"
                f"TP2   : {result_long['tp2']:.4f}"
            )

        # ——— GESTION DES SHORT ———
        result_short = analyze_market(symbol, df, side="short")
        if result_short:
            es_max = result_short["entry_max"]
            es_min = result_short["entry_min"]

            # 1) Anticipation SHORT
            if es_max <= last_price <= es_max * (1 + ANTICIPATION_THRESHOLD):
                send_telegram(
                    f"⏳ Anticipation SHORT {symbol}\n"
                    f"Le prix s'approche de la zone : {es_max:.4f} → {es_min:.4f}\n"
                    f"Prix actuel : {last_price:.4f}"
                )

            # 2) Zone SHORT atteinte
            if es_max >= last_price >= es_min:
                send_telegram(
                    f"🚨 Zone de SHORT atteinte {symbol}\n"
                    f"Entrée possible entre {es_max:.4f} et {es_min:.4f}\n"
                    f"Prix actuel : {last_price:.4f}"
                )

            # 3) Signal SHORT final (avec SL/TP)
            send_telegram(
                f"🔻 SHORT {symbol}\n"
                f"Entry : {result_short['entry_price']:.4f}\n"
                f"SL    : {result_short['stop_loss']:.4f}\n"
                f"TP1   : {result_short['tp1']:.4f}\n"
                f"TP2   : {result_short['tp2']:.4f}"
            )

        # Pause pour respecter les limites d'API
        time.sleep(1)

if __name__ == "__main__":
    while True:
        scan_symbols()
        # Attendre 1 minute avant le prochain scan
        time.sleep(60)
