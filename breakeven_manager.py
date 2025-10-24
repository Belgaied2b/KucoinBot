# breakeven_manager.py
import time, logging, threading, os, requests
from typing import Optional, Callable, Dict

from kucoin_utils import get_contract_info
from kucoin_trader import (
    modify_stop_order,
    get_open_position,
    get_mark_price,                      # fallback markPrice public (pour logs/info)
    place_reduce_only_tp_limit,          # pour poser TP2 si absent
    list_open_orders,                    # pour détecter TP2 existant / debug
)

LOGGER = logging.getLogger(__name__)

# =====================================================================
# 🔔 Alerte Telegram (optionnelle, utilisée via notifier)
# =====================================================================
TELEGRAM_TOKEN = os.getenv("TG_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")

def telegram_notifier(msg: str):
    """Envoie un message Telegram (si les variables TG_TOKEN et TG_CHAT_ID sont définies)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        LOGGER.debug("[TG] Token/Chat ID manquant — message non envoyé: %s", msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=5
        )
        LOGGER.info("[TG] Message envoyé: %s", msg)
    except Exception as e:
        LOGGER.warning("[TG] Erreur envoi Telegram: %s", e)

# =====================================================================
# 🧵 Registry pour éviter les doublons de monitor par symbole
# =====================================================================
_ACTIVE_MONITORS: Dict[str, threading.Thread] = {}
_ACTIVE_LOCK = threading.Lock()

def _notify_wrap(notifier: Optional[Callable[[str], None]], msg: str):
    if notifier:
        try:
            notifier(msg)
        except Exception:
            pass
    LOGGER.info(msg)

# =====================================================================
# 🔎 Vérifie la présence d’un TP LIMIT reduce-only à un prix proche
# =====================================================================
def _has_open_tp_at_price(symbol: str, side: str, price: float, tick: float) -> bool:
    """Vérifie s'il existe déjà un TP LIMIT reduce-only à ~ce prix (±0.5 tick)."""
    try:
        items = list_open_orders(symbol)
        if not items:
            return False
        opp_side = "sell" if side.lower() == "buy" else "buy"
        tol = max(tick * 0.5, 0.0)
        pmin, pmax = (price - tol, price + tol)
        for o in items:
            otype = (o.get("type") or o.get("orderType") or "").lower()
            o_side = (o.get("side") or "").lower()
            ro = bool(o.get("reduceOnly"))
            p = o.get("price")
            if p is None:
                continue
            try:
                op = float(p)
            except Exception:
                continue
            if otype == "limit" and ro and o_side == opp_side and pmin <= op <= pmax:
                return True
    except Exception:
        return False
    return False

# =====================================================================
# 🧠 Fonction principale : monitoring du BE / TP2
# =====================================================================
def monitor_breakeven(
    symbol: str,
    side: str,
    entry: float,
    tp1: float,
    tp2: Optional[float] = None,             # ← prix TP2 (optionnel)
    price_tick: Optional[float] = None,      # tick (optionnel, sinon meta)
    notifier: Optional[Callable[[str], None]] = None,  # callback (optionnel)
):
    """
    Surveille la position :
      - Cas >=2 lots : détecte TP1 par réduction de taille (~50% exécutés), déplace SL -> BE et s'assure de TP2.
      - Cas 1 lot    : pas de split possible → déplace SL -> BE quand le prix atteint TP1. (Pas de TP2 ici)
    IMPORTANT : on ne ferme rien au marché ici (les TP sont des LIMIT reduce-only).
    """
    try:
        # --- Normalisation côté ---
        side = (str(side) if side is not None else "").lower()
        if side not in ("buy", "sell"):
            try:
                pos_side = ((get_open_position(symbol) or {}).get("side") or "").lower()
                if pos_side in ("buy", "sell"):
                    side = pos_side
            except Exception:
                pass
        if side not in ("buy", "sell"):
            LOGGER.warning("[BE] %s -> side invalide '%s', fallback 'buy'", symbol, side)
            side = "buy"

        # --- Tick / tolérance ---
        tick_from_arg = float(price_tick) if (price_tick is not None) else 0.0
        try:
            meta = get_contract_info(symbol) or {}
            tick_from_meta = float(meta.get("tickSize", 0.0) or 0.0)
        except Exception:
            tick_from_meta = 0.0

        ref = max(abs(entry), abs(tp1), abs(tp2 or 0.0), 1e-9)
        if tick_from_arg <= 0 or tick_from_arg > 0.01 * ref:
            tick = tick_from_meta if tick_from_meta > 0 else 0.0
        else:
            tick = tick_from_arg if tick_from_arg > 0 else (tick_from_meta if tick_from_meta > 0 else 0.0)

        gap = abs(tp1 - entry)
        raw_tol = (tick * 2.0) if tick > 0 else 0.0
        cap = gap * 0.25
        tol = min(raw_tol, cap) if cap > 0 else raw_tol

        _notify_wrap(notifier, f"[BE] Monitoring {symbol} | side {side} | entry {entry:.10f} | TP1 {tp1:.10f} | TP2 {tp2 if tp2 is not None else '-'} | tick {tick:.10f} | tol {tol:.10f}")

        # --- Boucle principale ---
        initial_lots: Optional[int] = None
        target_lots_after_tp1: Optional[int] = None
        seen_reduction = False

        while True:
            try:
                pos = get_open_position(symbol) or {}
                cur_lots = int(float(pos.get("currentQty", 0) or 0))

                if initial_lots is None:
                    initial_lots = max(0, cur_lots)
                    if initial_lots >= 2:
                        target_lots_after_tp1 = (initial_lots + 1) // 2  # ceil
                        _notify_wrap(notifier, f"[BE] {symbol} initLots={initial_lots} → targetAfterTP1={target_lots_after_tp1}")
                    else:
                        _notify_wrap(notifier, f"[BE] {symbol} initLots={initial_lots} (no split possible) → fallback: price-based BE at TP1")

                if cur_lots <= 0:
                    LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                    break

                # -------------- CAS 1 LOT --------------
                if initial_lots == 1:
                    try:
                        mark = pos.get("markPrice") or get_mark_price(symbol)
                        mark_price = float(mark)
                    except Exception:
                        time.sleep(1.2)
                        continue

                    progress_ok = (mark_price >= entry) if side == "buy" else (mark_price <= entry)
                    if side == "buy":
                        tp_hit = progress_ok and (mark_price >= (tp1 - tol))
                    else:
                        tp_hit = progress_ok and (mark_price <= (tp1 + tol))

                    if tp_hit:
                        try:
                            modify_stop_order(
                                symbol=symbol, side=side,
                                existing_order_id=None,
                                new_stop=float(entry),
                                size_lots=cur_lots,
                            )
                            _notify_wrap(notifier, f"[BE] {symbol} ✅ TP1 atteint — SL déplacé à BE ({float(entry):.10f}) sur {cur_lots} lot")
                        except Exception as e:
                            LOGGER.exception("[BE] %s modify_stop_order (1-lot) a échoué: %s", symbol, e)
                        break
                    time.sleep(1.2)
                    continue

                # -------------- CAS >= 2 LOTS --------------
                if not seen_reduction:
                    if cur_lots < initial_lots:
                        seen_reduction = True
                    else:
                        time.sleep(1.2)
                        continue

                if target_lots_after_tp1 and cur_lots <= target_lots_after_tp1:
                    _notify_wrap(notifier, f"[BE] {symbol} ✅ TP1 détecté par réduction: lots {initial_lots} ➜ {cur_lots}")

                    # 1️⃣ Déplace SL → BE
                    try:
                        modify_stop_order(
                            symbol=symbol,
                            side=side,
                            existing_order_id=None,
                            new_stop=float(entry),
                            size_lots=cur_lots,
                        )
                        _notify_wrap(notifier, f"[BE] {symbol} 🛡️ SL déplacé à Break Even ({float(entry):.10f}) sur {cur_lots} lots")
                    except Exception as e:
                        LOGGER.exception("[BE] %s modify_stop_order a échoué: %s", symbol, e)

                    # 2️⃣ Pose TP2 si manquant
                    if (tp2 is not None) and cur_lots > 0:
                        try:
                            if not _has_open_tp_at_price(symbol, side, float(tp2), tick):
                                r = place_reduce_only_tp_limit(symbol, side, take_profit=float(tp2), size_lots=cur_lots)
                                if r.get("ok"):
                                    _notify_wrap(notifier, f"[BE] {symbol} 🎯 TP2 posé à {float(tp2):.10f} pour {cur_lots} lots")
                                else:
                                    LOGGER.error("[BE] Pose TP2 a échoué %s -> %s", symbol, r)
                            else:
                                _notify_wrap(notifier, f"[BE] {symbol} ℹ️ TP2 déjà présent autour de {float(tp2):.10f}")
                        except Exception as e:
                            LOGGER.exception("[BE] %s place_reduce_only_tp_limit a échoué: %s", symbol, e)

                    break
                time.sleep(1.2)
            except Exception as e:
                LOGGER.exception("[BE] erreur sur %s: %s", symbol, e)
                time.sleep(2.0)
    finally:
        # Nettoyage du registry si ce thread est celui enregistré
        with _ACTIVE_LOCK:
            th = _ACTIVE_MONITORS.get(symbol)
            if th is threading.current_thread():
                _ACTIVE_MONITORS.pop(symbol, None)
                LOGGER.debug("[BE] monitor %s libéré", symbol)

# =====================================================================
# 🚀 Lancement thread (anti-doublon + notifier par défaut via Telegram)
# =====================================================================
def launch_breakeven_thread(
    symbol: str,
    side: str,
    entry: float,
    tp1: float,
    tp2: Optional[float] = None,
    price_tick: Optional[float] = None,
    notifier: Optional[Callable[[str], None]] = None,
):
    """
    Lance un thread indépendant pour surveiller le TP1.
    - Un seul monitor par symbole est autorisé.
    - Si notifier n'est pas fourni mais TG_TOKEN/TG_CHAT_ID sont définis,
      on utilisera telegram_notifier automatiquement.
    """
    if notifier is None and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        notifier = telegram_notifier

    with _ACTIVE_LOCK:
        th = _ACTIVE_MONITORS.get(symbol)
        if th and th.is_alive():
            LOGGER.info("[BE] monitor déjà actif pour %s -> skip", symbol)
            return
        t = threading.Thread(
            target=monitor_breakeven,
            args=(symbol, side, entry, tp1, tp2, price_tick, notifier),
            daemon=True,
            name=f"BE_{symbol}",
        )
        _ACTIVE_MONITORS[symbol] = t
        t.start()

# =====================================================================
# 🧾 Debug des ordres ouverts
# =====================================================================
def debug_open_orders(symbol: str):
    """Affiche les ordres ouverts (évite le faux 404 du /openOrders direct)."""
    try:
        time.sleep(0.25)
        items = list_open_orders(symbol)
        LOGGER.info("[DBG] %s open orders: %d", symbol, len(items))
        for o in items[:6]:
            LOGGER.info(
                "  - id=%s side=%s type=%s price=%s stopPrice=%s size=%s reduceOnly=%s postOnly=%s status=%s",
                o.get("id") or o.get("orderId"),
                o.get("side"),
                o.get("type") or o.get("orderType"),
                o.get("price"),
                o.get("stopPrice"),
                o.get("size"),
                o.get("reduceOnly"),
                o.get("postOnly"),
                o.get("status"),
            )
    except Exception as e:
        LOGGER.warning("[DBG] list_open_orders failed for %s: %s", symbol, e)
