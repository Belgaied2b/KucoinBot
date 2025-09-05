# -*- coding: utf-8 -*-
"""
main.py — Boucle event-driven (recommandée)
Relie: ws_router.EventBus + PollingSource → analyze_signal → risk_guard → execution_sfi
"""

import os, asyncio, logging, math
from typing import Dict, Any
from ws_router import EventBus, PollingSource
from execution_sfi import SFIEngine
from risk_guard import RiskGuard
from meta_policy import MetaPolicy
from perf_metrics import register_signal_perf, update_perf_for_symbol
from kucoin_utils import fetch_all_symbols, fetch_klines  # <-- ajout de fetch_klines

# Utilise le bridge si dispo
try:
    import analyze_bridge as analyze_signal
except Exception:
    import analyze_signal  # fallback

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def fmt_price(x):
    if x is None: return "—"
    if x == 0: return "0"
    d = 2 if x >= 1 else min(8, int(abs(math.log10(1.0/x))) + 2)
    return f"{x:.{d}f}"

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("[TG OFF] %s", text); return
    import requests
    url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode":"Markdown", "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        logging.error("Telegram KO: %s", e)

async def handle_symbol_event(ev: Dict[str, Any], rg: RiskGuard, policy: MetaPolicy):
    symbol = ev.get("symbol")
    if ev.get("type") not in ("top", "bar"):
        return

    # --- Prépare H1/H4 (corrige le manque de df_h1/df_h4)
    try:
        h1_lim = int(os.getenv("H1_LIMIT", "500"))
        h4_lim = int(os.getenv("H4_LIMIT", "400"))
        df_h1 = fetch_klines(symbol, interval="1h", limit=h1_lim)
        df_h4 = fetch_klines(symbol, interval="4h", limit=h4_lim)
    except Exception as e:
        logging.warning("[%s] fetch_klines KO: %s", symbol, e)
        return

    # --- Analyse avec DF fournis
    try:
        res = analyze_signal.analyze_signal(symbol=symbol, df_h1=df_h1, df_h4=df_h4)
    except Exception as e:
        logging.warning("[%s] analyze_signal KO: %s", symbol, e)
        return

    if not isinstance(res, dict) or not res.get("valid", False):
        update_perf_for_symbol(symbol)
        return

    ok, reason = rg.can_enter(symbol, ws_latency_ms=50, last_data_age_s=5)
    if not ok:
        logging.info("[%s] Blocké par risk_guard: %s", symbol, reason)
        return

    arm, weight, label = policy.choose({"atr_pct": res.get("atr_pct", 0), "adx_proxy": res.get("adx_proxy", 0)})
    if weight < 0.25 and res.get("rr", 0) < 1.5:
        update_perf_for_symbol(symbol)
        return

    side = res.get("side", "long").lower()
    entry = res.get("entry")
    sl, tp1, tp2 = res.get("sl"), res.get("tp1"), res.get("tp2")
    value_usdt = float(os.environ.get("ORDER_VALUE_USDT", "20"))

    eng = SFIEngine(symbol, side, value_usdt, sl, tp1, tp2)
    orders = eng.place_initial(entry_hint=entry)
    eng.maybe_requote()

    msg = (f"🧠 *{symbol}* — *{side.upper()}* via *{label}*\n"
           f"RR: *{res.get('rr','—')}*  |  Entrée: *{fmt_price(entry)}*  |  SL: *{fmt_price(sl)}*  |  TP1: *{fmt_price(tp1)}*  TP2: *{fmt_price(tp2)}*\n"
           f"Ordres: {orders}")
    send_telegram(msg)

    key = f"{symbol}:{side}:{fmt_price(entry)}:{round(res.get('rr',0),2)}"
    register_signal_perf(key, symbol, side, entry)
    update_perf_for_symbol(symbol)

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        # fetch_all_symbols retourne des contrats USDT-M (…USDTM)
        symbols = [s for s in fetch_all_symbols() if s.endswith("USDTM")]
    except Exception:
        symbols = []
    if not symbols:
        logging.warning("Aucun symbole.")
        return

    bus = EventBus()
    src = PollingSource(symbols, interval_sec=5)
    bus.add_source(src.__aiter__())
    await bus.start()

    rg = RiskGuard()
    policy = MetaPolicy()

    async for ev in bus.events():
        try:
            await handle_symbol_event(ev, rg, policy)
        except Exception as e:
            logging.error("handle_symbol_event: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
