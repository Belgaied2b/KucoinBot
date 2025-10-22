"""
scanner.py — version robuste avec construction de signal safe (RR>0) + affichage Telegram clampé
- build_signal(): calcule entry/SL/TP cohérents selon le bias (LONG par défaut), RR > 0
- _fmt_signal_msg(): affiche RR clampé [0..10] ou 'n/a' si invalide
- Intégré au flux risk-first (sizing/guardrails/exits) si tu utilises déjà risk_manager/exits
"""
import time
import logging
import numpy as np
import pandas as pd

from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from telegram_client import send_telegram_message
from settings import DRY_RUN
from risk_manager import (
    reset_scan_counters, guardrails_ok, register_order, compute_vol_sizing
)
from exits import place_stop_loss, place_take_profit

LOGGER = logging.getLogger(__name__)
TOP_N = 60  # scanne moins, scanne mieux


# ---------- Helpers RR-safe ----------
def _atr14(df: pd.DataFrame) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return float(atr)

def build_signal(df: pd.DataFrame, symbol: str, bias: str = "LONG"):
    """
    Construit un signal avec entry/SL/TP cohérents, et RR>0.
    Retourne (signal_dict, err_str) ; si err_str != None => skip.
    """
    bias = (bias or "LONG").upper()
    entry = float(df["close"].iloc[-1])

    atr = _atr14(df)
    # garde-fous ATR
    if not np.isfinite(atr) or atr <= 0:
        # fallback minimum: 0.3% du prix
        atr = max(entry * 0.003, 1e-6)

    k_sl, k_tp = (1.5, 2.0)  # paramètres simples
    if bias == "LONG":
        sl = entry - k_sl * atr
        tp = entry + k_tp * atr
        stop_dist = entry - sl
        prof_dist = tp - entry
    else:
        sl = entry + k_sl * atr
        tp = entry - k_tp * atr
        stop_dist = sl - entry
        prof_dist = entry - tp

    # distances strictement > 0
    if (not np.isfinite(stop_dist)) or (not np.isfinite(prof_dist)) or stop_dist <= 0 or prof_dist <= 0:
        return None, f"Distances invalides (stop={stop_dist:.6g}, tp={prof_dist:.6g})"

    rr = prof_dist / stop_dist
    if (not np.isfinite(rr)) or rr <= 0:
        return None, f"RR invalide ({rr})"

    signal = {
        "symbol": symbol,
        "bias": bias,
        "entry": entry,
        "sl": sl,
        "tp1": entry + (prof_dist / 2 if bias == "LONG" else -prof_dist / 2),
        "tp2": tp,
        "rr_estimated": float(rr),
        "df": df,
        "ote": True
    }
    return signal, None


# ---------- Affichage Telegram safe ----------
def _fmt_signal_msg(sym, res):
    inst = res.get("institutional", {})
    inst_line = (
        f"{inst.get('institutional_score', 0)}/3 "
        f"({inst.get('institutional_strength', '?')}) — {inst.get('institutional_comment', '')}"
    )
    rr_val = res.get("rr", None)
    if rr_val is None or not np.isfinite(rr_val) or rr_val <= 0:
        rr_txt = "n/a"
    else:
        # clamp visuel pour éviter les valeurs absurdes
        rr_txt = f"{max(min(rr_val, 10.0), 0.0):.2f}"
    reasons = res.get("reasons") or []
    return (
        f"🔎 *{sym}* — score {res.get('score', 0):.1f} | RR {rr_txt}\n"
        f"Inst: {inst_line}\n"
        f"Notes: {', '.join(reasons) if reasons else 'OK'}"
    )


# ---------- Boucle principale ----------
def scan_and_send_signals():
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N)
    LOGGER.info("Start scan %d pairs", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            df = fetch_klines(sym, "1h", 250)
            if df.empty:
                LOGGER.warning("Skip %s (df empty)", sym)
                continue

            # Construit un signal RR-safe (LONG par défaut ici, adapte si tu as une logique de bias)
            signal, err = build_signal(df, sym, bias="LONG")
            if err:
                LOGGER.info("Skip %s -> %s", sym, err)
                continue

            res = evaluate_signal(signal)
            msg = _fmt_signal_msg(sym, res)

            if not res["valid"]:
                # Rejet clair et propre
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, "; ".join(res.get("reasons") or []))
                send_telegram_message("❌ " + msg)
                continue

            # ----- Risk-first sizing & guardrails -----
            meta = get_contract_info(sym)
            sizing = compute_vol_sizing(
                df=df,
                entry_price=signal["entry"],
                sl_price=signal["sl"],
                lot_multiplier=float(meta.get("multiplier", 1.0)),
                lot_size_min=int(meta.get("lotSize", 1)),
                tick_size=float(meta.get("tickSize", 0.01)),
            )
            ok, why = guardrails_ok(sym, sizing.notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                send_telegram_message(f"⏭️ {sym} — {why}")
                continue

            if DRY_RUN:
                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp=%.6f",
                            idx, len(pairs), sym, sizing.size_lots, sizing.price_rounded, signal["sl"], signal["tp2"])
                send_telegram_message("🧪 " + msg + f"\nLots: {sizing.size_lots} | Entry {sizing.price_rounded:.6f} | SL {signal['sl']:.6f} | TP {signal['tp2']:.6f}")
                register_order(sym, sizing.notional)
                continue

            # ----- Envoi ordre + exits -----
            order = place_limit_order(sym, "buy", sizing.price_rounded)
            LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)
            send_telegram_message("✅ " + msg + f"\nLots: {sizing.size_lots} | Entry {sizing.price_rounded:.6f} | SL {signal['sl']:.6f} | TP {signal['tp2']:.6f}")
            register_order(sym, sizing.notional)

            # Pose des exits (best-effort)
            sl_resp = place_stop_loss(sym, "buy", sizing.size_lots, signal["sl"])
            tp_resp = place_take_profit(sym, "buy", sizing.size_lots, signal["tp2"])
            LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

            time.sleep(0.7)  # anti-429
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
