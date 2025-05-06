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
EXCHANGES = ['kucoinfutures']
TF_LOW    = '1m'
TF_HIGH   = '15m'
RISK_PCT  = 0.01

async def main():
    logger.info("🚀 Démarrage de l’application – initialisation du DataStream")

    # Instanciation sans symboles
    ds = DataStream(EXCHANGES, [], TF_LOW)

    # Chargement des marchés CCXT Pro
    for name, ex in ds.exchanges.items():
        await ex.load_markets()
        logger.info(f"[{name}] marchés chargés ({len(ex.symbols)} symboles)")

    # Récupération des contracts actifs KuCoin (REST)
    perps = get_kucoin_perps()  # ex: ['BTCUSDTM', 'ETHUSDTM', ...]
    if not perps:
        logger.error("❌ Aucune perpetual renvoyée par get_kucoin_perps()")
        return

    # Mapping direct contract_id → symbole CCXT Pro
    symbols = []
    for contract in perps:
        for ex in ds.exchanges.values():
            m = ex.markets_by_id.get(contract)
            if m:
                symbols.append(m['symbol'])
                break

    symbols = list(dict.fromkeys(symbols))  # dé‐dup
    if not symbols:
        logger.error("❌ Aucun symbole CCXT Pro trouvé pour ces contracts REST")
        return

    logger.info(f"[main] Symboles pour le scan : {symbols}")
    ds.symbols = symbols

    alert_mgr = AlertManager(cooldown=300)

    async def handle_update(event_type, exch_name, symbol, data):
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

        for side in ('long','short'):
            res = analyze_market(symbol, df_low, side=side)
            if not res:
                continue
            if not confirm_multi_tf(symbol, df_low, df_high, side):
                continue
            obs = ExchangeAggregator(ds).get_orderbook(symbol)
            imb = detect_imbalance(obs)
            if (side=='long' and imb!='buy') or (side=='short' and imb!='sell'):
                continue

            bal = get_account_balance(symbol)
            risk_dist = (
                res['entry_price'] - res['stop_loss']
                if side=='long'
                else res['stop_loss'] - res['entry_price']
            )
            size = calculate_position_size(bal, RISK_PCT, risk_dist)

            key_base = (symbol, side, round(res['entry_price'],4))
            for alert_type in ('anticipation','zone','signal'):
                key = key_base + (alert_type,)
                if not alert_mgr.can_send(key):
                    continue

                img_b64 = generate_signal_chart(
                    df_low,
                    res['entry_min'],
                    res['entry_max'],
                    symbol,
                    TF_LOW
                )
                caption = (
                    f"🔔 {alert_type.upper()} {side.upper()} {symbol}\n"
                    f"Zone : {res['entry_min']:.4f}–{res['entry_max']:.4f}\n"
                    f"Prix : {df_low['close'].iat[-1]:.4f}\n"
                    f"Taille : {size:.4f}"
                )
                send_telegram(caption, image_b64=img_b64)

    # Lancement des WebSockets
    try:
        logger.info("🚀 Lancement des WebSocket KuCoinFutures")
        await ds.start(handle_update)
    finally:
        # fermeture propre
        for ex in ds.exchanges.values():
            await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
