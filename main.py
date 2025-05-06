import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)

import asyncio
import pandas as pd
import ccxt.pro as ccxtpro

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

async def main():
    logger.info("🚀 Application démarrage – initialisation du DataStream")

    # 1) Config de base
    EXCHANGES = ['kucoinfutures']
    TF_LOW, TF_HIGH = '1m', '15m'
    RISK_PCT = 0.01

    # 2) Instanciation du DataStream (symboles renseignés plus bas)
    ds = DataStream(EXCHANGES, [], TF_LOW)

    # 3) Chargement des marchés CCXT Pro
    logger.info("[main] Chargement des marchés KuCoin Futures")
    for ex in ds.exchanges.values():
        await ex.load_markets()
        logger.info(f"[{ex.id}] marchés chargés ({len(ex.symbols)} symboles)")

    # 4) Récupération des contracts actives via REST KuCoin
    perps = get_kucoin_perps()
    if not perps:
        logger.error("[main] Aucune perpetual contract retourné par get_kucoin_perps()")
        return

    # 5) Mapping REST IDs → symboles unifiés CCXT Pro
    symbols = []
    for contract in perps:
        for ex in ds.exchanges.values():
            m = ex.markets_by_id.get(contract)
            if m:
                symbols.append(m['symbol'])
                break
    symbols = list(dict.fromkeys(symbols))  # dé-dup
    logger.info(f"[main] Symboles scannés : {symbols}")
    ds.symbols = symbols

    alert_mgr = AlertManager(cooldown=300)

    # 6) Handler de chaque mise à jour WebSocket
    async def handle_update(event_type, exch, symbol, payload):
        logger.info(f"[handle_update] event={event_type} exchange={exch} symbol={symbol}")
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

                img_b64 = generate_signal_chart(
                    df_low,
                    res['entry_min'],
                    res['entry_max'],
                    symbol,
                    TF_LOW
                )
                caption = (
                    f"🔔 {alert_type.upper()} {side.upper()} {symbol}\n"
                    f"Zone d'entrée : {res['entry_min']:.4f} – {res['entry_max']:.4f}\n"
                    f"Prix actuel   : {df_low['close'].iloc[-1]:.4f}\n"
                    f"Taille        : {size:.4f}"
                )
                send_telegram(caption, image_b64=img_b64)

    # 7) Lancement du WebSocket
    try:
        logger.info("🚀 Démarrage des WebSocket KuCoinFutures")
        await ds.start(handle_update)
    finally:
        for ex in ds.exchanges.values():
            await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
