# main.py

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)

import asyncio
from data_stream import DataStream
from multi_exchange import ExchangeAggregator
from multi_tf import confirm_multi_tf
from orderbook_utils import detect_imbalance
from signal_analysis import analyze_market
from risk_manager import calculate_position_size
from alert_manager import AlertManager
from chart_generator import generate_signal_chart
from kucoin_utils import send_telegram, get_account_balance, get_kucoin_perps

logger = logging.getLogger(__name__)

# === CONFIGURATION ===
EXCHANGES = ['kucoinfutures']   # uniquement KuCoin Futures via ccxt.pro
TF_LOW    = '1m'
TF_HIGH   = '15m'
RISK_PCT  = 0.01

async def main():
    logger.info("🚀 Démarrage de l’application – initialisation du DataStream")

    # 1) Instanciation DataStream sans symboles pour le moment
    ds = DataStream(EXCHANGES, [], TF_LOW)

    # 2) Chargement des marchés CCXT Pro
    for name, ex in ds.exchanges.items():
        await ex.load_markets()
        logger.info(f"[{name}] marchés chargés ({len(ex.symbols)} symboles)")

    # 3) Récupération des contracts actifs via REST KuCoin
    perps = get_kucoin_perps()
    if not perps:
        logger.error("Aucun perpetual contract retourné par get_kucoin_perps() !")
        return

    # 4) Mapping REST IDs -> symboles CCXT Pro
    symbols = []
    for contract in perps:
        base_id = contract[:-1].upper()  # ex: 'BTCUSDTM' -> 'BTCUSDT'
        for ex in ds.exchanges.values():
            for sym in ex.symbols:
                cleaned = sym.replace('/', '').replace(':', '').upper()
                if cleaned.startswith(base_id):
                    symbols.append(sym)
                    break

    symbols = list(dict.fromkeys(symbols))  # suppression des doublons
    if not symbols:
        logger.error("Aucun symbol CCXT Pro trouvé pour les contracts REST !")
        return

    logger.info(f"[main] Symboles pour le scan : {symbols}")
    ds.symbols = symbols

    # 5) Préparation du scan
    alert_mgr = AlertManager(cooldown=300)

    async def handle_update(event_type, exch_name, symbol, payload):
        logger.info(f"[handle_update] {event_type=} {symbol=}")
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
            res = analyze_market(symbol, df_low, side=side)
            if not res:
                continue
            if not confirm_multi_tf(symbol, df_low, df_high, side):
                continue
            obs = ExchangeAggregator(ds).get_orderbook(symbol)
            imb = detect_imbalance(obs)
            if (side == 'long' and imb != 'buy') or (side == 'short' and imb != 'sell'):
                continue

            bal = get_account_balance(symbol)
            risk_dist = (
                res['entry_price'] - res['stop_loss']
                if side == 'long'
                else res['stop_loss'] - res['entry_price']
            )
            size = calculate_position_size(bal, RISK_PCT, risk_dist)

            key_base = (symbol, side, round(res['entry_price'], 4))
            for alert_type in ('anticipation', 'zone', 'signal'):
                key = key_base + (alert_type,)
                if not alert_mgr.can_send(key):
                    continue

                img_b64 = generate_signal_chart(df_low, res['entry_min'], res['entry_max'], symbol, TF_LOW)
                caption = (
                    f"🔔 {alert_type.upper()} {side.upper()} {symbol}\n"
                    f"Zone d'entrée : {res['entry_min']:.4f} – {res['entry_max']:.4f}\n"
                    f"Prix actuel   : {df_low['close'].iloc[-1]:.4f}\n"
                    f"Taille        : {size:.4f}"
                )
                send_telegram(caption, image_b64=img_b64)

    # 6) Lancement du WebSocket
    try:
        logger.info("🚀 Lancement des WebSocket KuCoinFutures")
        await ds.start(handle_update)
    finally:
        # fermeture propre des connexions ccxt.pro
        for ex in ds.exchanges.values():
            await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
