# breakeven_manager.py
import time
import logging
import threading
import os
import requests
import errno
import inspect
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
# 🧵 Registry (anti-doublon monitor par position) + TTL + File-lock inter-processus
# =====================================================================
_ACTIVE_MONITORS: Dict[str, threading.Thread] = {}
_ACTIVE_LAST_TS: Dict[str, float] = {}
_ACTIVE_LOCK = threading.Lock()
_MONITOR_TTL = 30.0  # secondes

_LOCK_DIR = "/tmp/kucoin_be_locks"
os.makedirs(_LOCK_DIR, exist_ok=True)

def _lock_path(key: str) -> str:
    safe_key = key.replace("/", "_").replace(" ", "_")
    return os.path.join(_LOCK_DIR, f"{safe_key}.lock")

def _try_acquire_file_lock(key: str, ttl: float) -> bool:
    """
    Crée un lockfile par key. Si le lock existe et est récent (< ttl), on refuse.
    Sinon on (ré)écrit le lockfile avec l'heure courante.
    """
    path = _lock_path(key)
    now = time.time()
    try:
        if os.path.exists(path):
            try:
                mtime = os.path.getmtime(path)
                if (now - mtime) < max(1.0, ttl):
                    return False
            except Exception:
                pass
        with open(path, "w") as f:
            f.write(str(now))
        return True
    except OSError as e:
        if e.errno not in (errno.EACCES, errno.EPERM):
            LOGGER.debug("[BE] lock create error for %s: %s", key, e)
        return False

def _refresh_file_lock_ts(key: str):
    """Met à jour le mtime du lock (keep-alive)."""
    try:
        with open(_lock_path(key), "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass

def _release_file_lock(key: str):
    try:
        os.remove(_lock_path(key))
    except Exception:
        pass

# --- Coalescing de logs "skip" pour éviter les doublons visuels ---
_SKIP_LOG_GUARD: Dict[str, float] = {}
_SKIP_LOG_TTL = 10.0  # secondes

def _log_skip_once(key: str, msg: str):
    now = time.time()
    last = _SKIP_LOG_GUARD.get(key, 0.0)
    if (now - last) >= _SKIP_LOG_TTL:
        LOGGER.info(msg)
        _SKIP_LOG_GUARD[key] = now
    else:
        LOGGER.debug(msg)

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
# ⚠️ Garde d’appelant (optionnelle)
# =====================================================================
BE_ONLY_FROM_EXITS = os.getenv("BE_ONLY_FROM_EXITS", "1")  # "0" pour désactiver

def _caller_hint() -> str:
    try:
        st = inspect.stack()
        if len(st) >= 3:
            f = st[2]
            return f"{f.filename}:{f.lineno} in {f.function}"
    except Exception:
        pass
    return "unknown"

def _caller_allowed() -> bool:
    if BE_ONLY_FROM_EXITS != "1":
        return True
    c = _caller_hint().replace("\\", "/")
    return ("exits_manager.py" in c) or ("breakeven_manager.py" in c)

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
    # --- Garde d'appelant (bloque les appels directs non autorisés) ---
    if not _caller_allowed():
        LOGGER.warning("[BE] monitor launch blocked: not called from exits_manager (%s)", _caller_hint())
        return

    # --- Obtenir une clé robuste AVANT tout (pour lock inter-processus) ---
    _, pos_id_for_key, _ = _get_pos_snapshot(symbol)
    key_for_lock = _make_monitor_key(symbol, entry, pos_id_for_key)

    # --- File-lock à l'entrée (bloque les doubles, même si appel direct) ---
    lock_held = _try_acquire_file_lock(key_for_lock, _MONITOR_TTL)
    if not lock_held:
        _log_skip_once(key_for_lock, f"[BE] file-lock present -> skip monitor start (key={key_for_lock})")
        return

    try:
        # -- Tick --
        try:
            meta = get_contract_info(symbol) or {}
            tick_meta = float(meta.get("tickSize", 0.0) or 0.0)
        except Exception:
            tick_meta = 0.0
        tick = float(price_tick) if (price_tick and price_tick > 0) else tick_meta

        # Corrige un tick aberrant
        ref = max(abs(entry), abs(tp1), abs(tp2 or 0.0), 1.0)
        if tick <= 0 or tick > max(0.01 * ref, 0.01):
            try:
                meta2 = get_contract_info(symbol) or {}
                tick2 = float(meta2.get("tickSize", 0.0) or 0.0)
                if tick2 > 0:
                    tick = tick2
            except Exception:
                pass
        if tick <= 0 or tick > max(0.01 * ref, 0.01):
            LOGGER.warning("[BE] %s tick aberrant (%.6f) — aborting this monitor to avoid duplicates.", symbol, tick)
            return  # on n'exécute pas ce monitor “mauvais” — l'autre (avec bon tick) reste actif

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

        # -- Clé finale pour logs / registry --
        cur_lots, pos_id, _ = _get_pos_snapshot(symbol)
        key = _make_monitor_key(symbol, entry_r, pos_id)

        _notify_wrap(notifier, f"[BE] Monitoring {symbol} | key={key} | side {side} | entry {entry_r:.10f} | TP1 {tp1_r:.10f} | TP2 {tp2_r if tp2_r is not None else '-'} | tick {tick:.10f}")
        initial_lots: Optional[int] = None
        target_lots_after_tp1: Optional[int] = None
        moved_to_be = False  # idempotence

        # Boucle principale
        while True:
            _refresh_file_lock_ts(key_for_lock)  # keep-alive du lock
            lots, pos_id_now, mark_price = _get_pos_snapshot(symbol)

            # si position fermée → fin
            if lots <= 0:
                LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                break

            # init
            if initial_lots is None:
                initial_lots = lots
                if initial_lots >= 2:
                    target_lots_after_tp1 = (initial_lots + 1) // 2  # ceil(initial/2)
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
        # Nettoyage registry (si thread enregistré) + release lock si acquis ici
        with _ACTIVE_LOCK:
            # key basé sur entry arrondi si dispo, sinon celui du lock
            try:
                cur_lots, pos_id, _ = _get_pos_snapshot(symbol)
                tick_meta = (get_contract_info(symbol) or {}).get("tickSize", 0.0) or 0.0
                key_final = _make_monitor_key(symbol, _round_to_tick(float(entry), float(tick_meta)), pos_id)
            except Exception:
                key_final = key_for_lock
            th = _ACTIVE_MONITORS.get(key_final)
            if th is threading.current_thread():
                _ACTIVE_MONITORS.pop(key_final, None)
                _ACTIVE_LAST_TS[key_final] = time.time()
                LOGGER.debug("[BE] monitor %s libéré", key_final)
        _release_file_lock(key_for_lock)

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
    - Verrou inter-processus (file-lock) pour empêcher les doubles monitors cross-workers.
    - Garde optionnelle : seul exits_manager.py peut lancer (BE_ONLY_FROM_EXITS="1" par défaut).
    """
    caller = _caller_hint()
    LOGGER.info("[BE] launch_breakeven_thread called by %s", caller)
    if BE_ONLY_FROM_EXITS == "1" and "exits_manager.py" not in caller.replace("\\", "/"):
        LOGGER.warning("[BE] launch blocked: not called from exits_manager (%s)", caller)
        return

    if notifier is None and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        notifier = telegram_notifier

    # Clé (tente de lire positionId)
    try:
        pos = get_open_position(symbol) or {}
        pos_id = None
        for k in ("positionId", "id", "positionID"):
            if k in pos and pos.get(k):
                pos_id = str(pos.get(k))
                break
    except Exception:
        pos_id = None

    # Normalise/assainit le tick pour la clé (même logique que monitor)
    try:
        meta = get_contract_info(symbol) or {}
        tick_meta = float(meta.get("tickSize", 0.0) or 0.0)
    except Exception:
        tick_meta = 0.0
    tick = float(price_tick) if (price_tick and price_tick > 0) else tick_meta
    ref = max(abs(entry), abs(tp1), abs(tp2 or 0.0), 1.0)
    if tick <= 0 or tick > max(0.01 * ref, 0.01):
        tick = tick_meta  # on ne fige pas ici un mauvais tick dans la clé

    entry_r = _round_to_tick(float(entry), tick)
    key = _make_monitor_key(symbol, entry_r, pos_id)

    # Verrou inter-processus (évite doubles monitors cross-workers)
    if not _try_acquire_file_lock(key, _MONITOR_TTL):
        _log_skip_once(key, f"[BE] file-lock present -> skip launch (key={key})")
        return

    with _ACTIVE_LOCK:
        # déjà actif ?
        th = _ACTIVE_MONITORS.get(key)
        if th and th.is_alive():
            _log_skip_once(key, f"[BE] monitor déjà actif -> skip (key={key})")
            _release_file_lock(key)
            return

        # TTL contre relance immédiate
        last_ts = _ACTIVE_LAST_TS.get(key, 0.0)
        if (time.time() - last_ts) < _MONITOR_TTL:
            _log_skip_once(key, f"[BE] monitor récemment terminé -> skip (key={key}, TTL)")
            _release_file_lock(key)
            return

        t = threading.Thread(
            target=monitor_breakeven,
            args=(symbol, side, entry, tp1, tp2, price_tick, notifier),
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
