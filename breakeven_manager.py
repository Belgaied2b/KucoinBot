# breakeven_manager.py
import time
import logging
import threading
import os
import requests
from typing import Optional, Callable, Dict, Tuple

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
# 🧰 Utils prix / ticks / clés
# =====================================================================
def _round_to_tick(x: float, tick: float) -> float:
    """Arrondit au tick le plus proche (sécurisé contre les binaires flottants)."""
    if tick and tick > 0:
        steps = round(float(x) / float(tick))
        return round(steps * float(tick), 12)
    return float(x)

def _approx_price_equal(a: float, b: float, tick: float, tol_ticks: float = 0.5) -> bool:
    """Compare deux prix avec une tolérance (par défaut ±0.5 tick)."""
    if tick and tick > 0:
        return abs(float(a) - float(b)) <= (float(tick) * float(tol_ticks))
    # sans tick, tolérance très faible
    return abs(float(a) - float(b)) <= max(abs(a), abs(b), 1.0) * 1e-9

def _make_monitor_key(symbol: str, entry: float, pos_id: Optional[str]) -> str:
    """Clé de monitor : privilégie positionId s’il existe, sinon symbol+entry."""
    if pos_id:
        return f"{symbol}#pos:{pos_id}"
    return f"{symbol}#e:{round(float(entry), 8)}"

def _notify_wrap(notifier: Optional[Callable[[str], None]], msg: str):
    if notifier:
        try:
            notifier(msg)
        except Exception:
            pass
    LOGGER.info(msg)

# =====================================================================
# 🧵 Registry (anti-doublon monitor par position) + TTL
# =====================================================================
_ACTIVE_MONITORS: Dict[str, threading.Thread] = {}
_ACTIVE_LAST_TS: Dict[str, float] = {}
_ACTIVE_LOCK = threading.Lock()
_MONITOR_TTL = 30.0  # secondes

# =====================================================================
# 🔎 Aides ordres ouverts
# =====================================================================
def _has_open_tp_at_price(symbol: str, side: str, price: float, tick: float) -> bool:
    """Vérifie s'il existe déjà un TP LIMIT reduce-only du côté opposé autour du prix (±0.5 tick)."""
    try:
        items = list_open_orders(symbol) or []
        opp_side = "sell" if (side or "").lower() == "buy" else "buy"
        for o in items:
            otype = (o.get("type") or o.get("orderType") or "").lower()
            o_side = (o.get("side") or "").lower()
            ro = str(o.get("reduceOnly")).lower() == "true"
            p = o.get("price")
            if not (otype and "limit" in otype and ro and o_side == opp_side and p is not None):
                continue
            try:
                if _approx_price_equal(float(p), float(price), tick, tol_ticks=0.5):
                    return True
            except Exception:
                continue
    except Exception as e:
        LOGGER.debug("[BE] _has_open_tp_at_price error on %s: %s", symbol, e)
    return False

def _get_pos_snapshot(symbol: str) -> Tuple[int, Optional[str], float]:
    """
    Retourne (current_lots, positionId, markPrice_float).
    - current_lots = 0 si pas de position.
    - positionId peut être None si non fourni par l'API.
    - markPrice_float fallback via get_mark_price si absent.
    """
    try:
        pos = get_open_position(symbol) or {}
        lots = int(float(pos.get("currentQty", 0) or 0))
        pos_id = None
        # champs possibles: "positionId", "id" ou autres selon wrapper
        for k in ("positionId", "id", "positionID"):
            if k in pos and pos.get(k):
                pos_id = str(pos.get(k))
                break
        try:
            mp = float(pos.get("markPrice")) if pos.get("markPrice") is not None else float(get_mark_price(symbol))
        except Exception:
            mp = 0.0
        return max(0, lots), pos_id, mp
    except Exception as e:
        LOGGER.debug("[BE] _get_pos_snapshot error on %s: %s", symbol, e)
        return 0, None, 0.0

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
      - Cas 1 lot    : pas de split → déplace SL -> BE quand le prix atteint TP1. (Pas de TP2 ici)
    IMPORTANT : pas de close market ici (TP = LIMIT reduce-only).
    """
    # -- Tick --
    try:
        meta = get_contract_info(symbol) or {}
        tick_meta = float(meta.get("tickSize", 0.0) or 0.0)
    except Exception:
        tick_meta = 0.0
    tick = float(price_tick) if (price_tick and price_tick > 0) else tick_meta
    entry_r = _round_to_tick(float(entry), tick)
    tp1_r   = _round_to_tick(float(tp1), tick)
    tp2_r   = _round_to_tick(float(tp2), tick) if tp2 is not None else None

    # -- Side normalisé --
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

    # -- Clé monitor (par position) & enregistrement anti-doublon --
    cur_lots, pos_id, _ = _get_pos_snapshot(symbol)
    key = _make_monitor_key(symbol, entry_r, pos_id)

    _notify_wrap(notifier, f"[BE] Monitoring {symbol} | key={key} | side {side} | entry {entry_r:.10f} | TP1 {tp1_r:.10f} | TP2 {tp2_r if tp2_r is not None else '-'} | tick {tick:.10f}")
    initial_lots: Optional[int] = None
    target_lots_after_tp1: Optional[int] = None
    moved_to_be = False  # idempotence
    started_ts = time.time()

    try:
        while True:
            lots, pos_id_now, mark_price = _get_pos_snapshot(symbol)

            # si position fermée → fin
            if lots <= 0:
                LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                break

            # init
            if initial_lots is None:
                initial_lots = lots
                if initial_lots >= 2:
                    # target -> ceil(initial / 2)
                    target_lots_after_tp1 = (initial_lots + 1) // 2
                    _notify_wrap(notifier, f"[BE] {symbol} initLots={initial_lots} → targetAfterTP1={target_lots_after_tp1}")
                else:
                    _notify_wrap(notifier, f"[BE] {symbol} initLots={initial_lots} (no split) → fallback: prix atteint TP1 pour BE")

            # ---------- Cas 1 lot : BE au franchissement du prix ----------
            if initial_lots == 1:
                if mark_price == 0.0:
                    time.sleep(1.2)
                    continue
                progressed = (mark_price >= entry_r) if side == "buy" else (mark_price <= entry_r)
                if side == "buy":
                    tp_hit = progressed and (mark_price >= tp1_r or _approx_price_equal(mark_price, tp1_r, tick, 0.5))
                else:
                    tp_hit = progressed and (mark_price <= tp1_r or _approx_price_equal(mark_price, tp1_r, tick, 0.5))

                if tp_hit and not moved_to_be:
                    try:
                        modify_stop_order(
                            symbol=symbol, side=side,
                            existing_order_id=None,
                            new_stop=float(entry_r),
                            size_lots=lots,  # tout
                        )
                        moved_to_be = True
                        _notify_wrap(notifier, f"[BE] {symbol} ✅ TP1 atteint (1 lot) — SL → BE ({entry_r:.10f})")
                    except Exception as e:
                        LOGGER.exception("[BE] %s modify_stop_order (1-lot) a échoué: %s", symbol, e)
                    break  # rien d'autre à faire en 1 lot
                time.sleep(1.2)
                continue

            # ---------- Cas >= 2 lots : détection réduction ----------
            if target_lots_after_tp1 and lots <= target_lots_after_tp1:
                # TP1 exécuté → SL -> BE (une seule fois)
                if not moved_to_be:
                    try:
                        modify_stop_order(
                            symbol=symbol,
                            side=side,
                            existing_order_id=None,
                            new_stop=float(entry_r),
                            size_lots=lots,  # sur le restant
                        )
                        moved_to_be = True
                        _notify_wrap(notifier, f"[BE] {symbol} ✅ TP1 détecté : lots {initial_lots} ➜ {lots} — SL → BE ({entry_r:.10f})")
                    except Exception as e:
                        LOGGER.exception("[BE] %s modify_stop_order a échoué: %s", symbol, e)

                # TP2 : poser si manquant
                if tp2_r is not None and lots > 0:
                    try:
                        if not _has_open_tp_at_price(symbol, side, float(tp2_r), tick):
                            r = place_reduce_only_tp_limit(symbol, side, take_profit=float(tp2_r), size_lots=lots)
                            if r.get("ok"):
                                _notify_wrap(notifier, f"[BE] {symbol} 🎯 TP2 posé à {float(tp2_r):.10f} pour {lots} lots")
                            else:
                                LOGGER.error("[BE] Pose TP2 a échoué %s -> %s", symbol, r)
                        else:
                            _notify_wrap(notifier, f"[BE] {symbol} ℹ️ TP2 déjà présent ~ {float(tp2_r):.10f}")
                    except Exception as e:
                        LOGGER.exception("[BE] %s place_reduce_only_tp_limit a échoué: %s", symbol, e)

                break  # on sort après avoir géré BE + TP2
            else:
                # pas encore de réduction
                time.sleep(1.2)
                continue

    finally:
        # Nettoyage du registry si ce thread est celui enregistré
        with _ACTIVE_LOCK:
            th = _ACTIVE_MONITORS.get(key)
            if th is threading.current_thread():
                _ACTIVE_MONITORS.pop(key, None)
                _ACTIVE_LAST_TS[key] = time.time()
                LOGGER.debug("[BE] monitor %s libéré", key)

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
    - Un seul monitor par **position** (positionId si dispo, sinon symbol+entry).
    - Anti-spam via TTL 30s (si un monitor vient de se terminer, on évite de relancer immédiatement).
    - Si notifier n'est pas fourni mais TG_TOKEN/TG_CHAT_ID sont définis, on utilise telegram_notifier.
    """
    if notifier is None and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        notifier = telegram_notifier

    # Construire la clé (on essaie de lire la position pour capter positionId)
    try:
        pos = get_open_position(symbol) or {}
        pos_id = None
        for k in ("positionId", "id", "positionID"):
            if k in pos and pos.get(k):
                pos_id = str(pos.get(k))
                break
    except Exception:
        pos_id = None
    try:
        meta = get_contract_info(symbol) or {}
        tick_meta = float(meta.get("tickSize", 0.0) or 0.0)
    except Exception:
        tick_meta = 0.0
    tick = float(price_tick) if (price_tick and price_tick > 0) else tick_meta
    entry_r = _round_to_tick(float(entry), tick)

    key = _make_monitor_key(symbol, entry_r, pos_id)

    with _ACTIVE_LOCK:
        # déjà actif ?
        th = _ACTIVE_MONITORS.get(key)
        if th and th.is_alive():
            LOGGER.info("[BE] monitor déjà actif pour %s -> skip (key=%s)", symbol, key)
            return

        # TTL contre relance immédiate
        last_ts = _ACTIVE_LAST_TS.get(key, 0.0)
        if (time.time() - last_ts) < _MONITOR_TTL:
            LOGGER.info("[BE] monitor récemment terminé pour %s -> skip (key=%s, TTL)", symbol, key)
            return

        t = threading.Thread(
            target=monitor_breakeven,
            args=(symbol, side, entry_r, tp1, tp2, price_tick, notifier),
            daemon=True,
            name=f"BE_{key}",
        )
        _ACTIVE_MONITORS[key] = t
        t.start()

# =====================================================================
# 🧾 Debug des ordres ouverts
# =====================================================================
def debug_open_orders(symbol: str):
    """Affiche les ordres ouverts (évite le faux 404 du /openOrders direct)."""
    try:
        time.sleep(0.25)
        items = list_open_orders(symbol) or []
        LOGGER.info("[DBG] %s open orders: %d", symbol, len(items))
        for o in items[:8]:
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
