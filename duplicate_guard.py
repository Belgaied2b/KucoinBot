# duplicate_guard.py
from __future__ import annotations
import os, json, time, hashlib, threading
from typing import Any, Dict, Optional

CACHE_PATH = os.getenv("DUP_GUARD_PATH", "sent_signals.json")
_LOCK_DIR = "/tmp/kucoin_dup_locks"
os.makedirs(_LOCK_DIR, exist_ok=True)
_FILE_LOCK = threading.Lock()

def _lock_path() -> str:
    return os.path.join(_LOCK_DIR, "dup_guard.lock")

def _acquire_lock():
    _FILE_LOCK.acquire()

def _release_lock():
    _FILE_LOCK.release()

def _now() -> float:
    return time.time()

def _load_cache() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_cache(obj: Dict[str, Dict[str, Any]]) -> None:
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, CACHE_PATH)

def _round_bucket(price: Optional[float], bucket: float) -> Optional[float]:
    if price is None or bucket <= 0:
        return price
    return round(round(float(price) / bucket) * bucket, 12)

def _structure_signature(signal: Dict[str, Any]) -> str:
    """
    Construit une signature à partir des éléments de structure si dispo.
    Utilise ce qui existe sans planter si absent.
    """
    keys = [
        # structure_utils/analyze_signal potentiels :
        "bos_direction", "chos_direction", "choch_direction",
        "has_liquidity_zone", "liquidity_side",
        "engulfing", "fvg", "cos", "trend", "ema_state",
        # timeframes/mode
        "tf", "tf_confirm", "mode",
    ]
    bag = []
    for k in keys:
        v = signal.get(k)
        if v is not None:
            bag.append(f"{k}={v}")
    return "|".join(bag)

def signal_fingerprint(
    *,
    symbol: str,
    side: str,
    timeframe: str,
    entry_price: Optional[float],
    tick_size: Optional[float] = None,
    entry_bucket_ticks: int = 10,
    structure: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Empreinte stable: (symbol, side, timeframe, structure, entry_bucket).
    entry_bucket = entry arrondi à N ticks (évite re-signal pour 1 tick de diff).
    """
    t = (tick_size or 0.0)
    bucket = max(t * max(1, int(entry_bucket_ticks)), 0.0) if t > 0 else 0.0
    entry_bucket = _round_bucket(entry_price, bucket) if bucket > 0 else entry_price

    struct_sig = _structure_signature(structure or {})
    core = {
        "symbol": (symbol or "").upper(),
        "side": (side or "").lower(),
        "tf": (timeframe or "").lower(),
        "entry_bucket": entry_bucket,
        "structure_sig": struct_sig,
    }
    # Hash pour clé compacte
    raw = json.dumps(core, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return h

def is_duplicate_and_mark(
    fingerprint: str,
    *,
    ttl_seconds: int = 6 * 3600,
    mark: bool = True,
) -> bool:
    """
    True si fingerprint présent et non expiré. Si mark=True, enregistre/rafraîchit.
    """
    _acquire_lock()
    try:
        cache = _load_cache()
        now = _now()
        # purge expirés
        to_del = [k for k, v in cache.items() if v.get("exp", 0) < now]
        for k in to_del:
            cache.pop(k, None)

        if fingerprint in cache and cache[fingerprint].get("exp", 0) >= now:
            # déjà présent → doublon
            return True

        if mark:
            cache[fingerprint] = {
                "exp": now + ttl_seconds,
                "ts": now,
            }
            _save_cache(cache)
        return False
    finally:
        _release_lock()

def unmark(fingerprint: str) -> None:
    _acquire_lock()
    try:
        cache = _load_cache()
        if fingerprint in cache:
            cache.pop(fingerprint, None)
            _save_cache(cache)
    finally:
        _release_lock()

def purge_all() -> None:
    _acquire_lock()
    try:
        _save_cache({})
    finally:
        _release_lock()
