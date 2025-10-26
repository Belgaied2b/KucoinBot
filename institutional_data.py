"""
institutional_data.py — Analyse institutionnelle avancée (robuste)
- Mapping KuCoin → Binance (XBTUSDTM → BTCUSDT, etc.)
- Cache des symboles Binance pour valider le mapping
- Fetch tolérant (JSON vide, rate-limit, réponses non-JSON)
- Profil "large traders", divergence CVD, liquidity map
- Couche d’orchestration (pondération + commentaire)
"""
from __future__ import annotations

import time
import logging
import requests
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any
from functools import lru_cache

LOGGER = logging.getLogger(__name__)

# Endpoints Binance
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1"
BINANCE_FUTURES_DATA = "https://fapi.binance.com/futures/data"  # ← correct pour topLongShortAccountRatio

# =======================
#   CACHES & THROTTLING
# =======================
_BINANCE_SYMS_CACHE: Dict[str, Any] = {"ts": 0.0, "set": set()}
_BINANCE_SYMS_TTL = 15 * 60  # 15 minutes

# Compteurs santé / anti-spam logs
_ERR_COUNTS: Dict[str, int] = {"large_ratio": 0, "cvd_div": 0, "map": 0, "net": 0}
_ERR_WARN_EVERY = 10  # WARNING toutes les 10 occurrences

# Throttle par endpoint (rps raisonnable pour éviter 429)
_MIN_INTERVAL_S = 0.25  # ~4 req/s par endpoint
_LAST_CALL_TS: Dict[str, float] = {}

# Session HTTP réutilisée
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "insto-bot/1.0 (+binance)"})


def _bump_and_maybe_warn(key: str, msg: str, detail: str = ""):
    _ERR_COUNTS[key] = _ERR_COUNTS.get(key, 0) + 1
    c = _ERR_COUNTS[key]
    line = f"[INST] {msg}" + (f" | {detail}" if detail else "")
    if c % _ERR_WARN_EVERY == 0:
        LOGGER.warning("%s (x%s)", line, c)
    else:
        LOGGER.debug("%s", line)


def _throttle(endpoint_key: str):
    """Respecte un délai minimal entre deux appels du même endpoint."""
    last = _LAST_CALL_TS.get(endpoint_key, 0.0)
    now = time.time()
    wait = _MIN_INTERVAL_S - (now - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL_TS[endpoint_key] = time.time()


def _safe_json_get(url: str, params: Optional[dict] = None, timeout: float = 7.0, retries: int = 2):
    """
    GET tolérant:
    - throttle local par endpoint
    - retry simple (incl. 429 via Retry-After)
    - parse JSON sûr (retourne None sinon)
    - ignore codes d'erreur Binance {"code":..., "msg":...} non 0
    """
    endpoint_key = url.split("/fapi/")[-1].split("?")[0]
    for attempt in range(retries + 1):
        try:
            _throttle(endpoint_key)
            r = _SESSION.get(url, params=params or {}, timeout=timeout)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    _bump_and_maybe_warn("net", "Non-JSON response", r.text[:120])
                    data = None
                # Certaines erreurs Binance renvoient {"code": -xxxx, "msg": "..."}
                if isinstance(data, dict) and "code" in data and data.get("code") not in (0, None):
                    _bump_and_maybe_warn("net", "Binance error", f"{data}")
                    data = None
                return data
            elif r.status_code == 429:
                # Respecte Retry-After si fourni
                ra = r.headers.get("Retry-After")
                try:
                    sleep_s = float(ra) if ra else 1.2
                except Exception:
                    sleep_s = 1.2
                time.sleep(sleep_s)
                continue
            else:
                _bump_and_maybe_warn("net", f"HTTP {r.status_code} on {endpoint_key}")
        except Exception as e:
            _bump_and_maybe_warn("net", "Request exception", str(e))
        # Backoff léger entre tentatives
        time.sleep(0.4 * (attempt + 1))
    return None


def _refresh_binance_symbols():
    """Charge et met en cache la liste des symboles USDT futures (ex: BTCUSDT)."""
    now = time.time()
    if (now - _BINANCE_SYMS_CACHE["ts"]) < _BINANCE_SYMS_TTL and _BINANCE_SYMS_CACHE["set"]:
        return
    data = _safe_json_get(f"{BINANCE_FUTURES_API}/exchangeInfo", params=None, timeout=6.0, retries=2)
    syms = set()
    # Attendu: {"symbols": [{"symbol":"BTCUSDT","contractType":"PERPETUAL",...}, ...]}
    try:
        for s in (data or {}).get("symbols", []):
            if s.get("contractType") in ("PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER"):
                name = str(s.get("symbol") or "").upper()
                if name.endswith("USDT"):
                    syms.add(name)
    except Exception:
        pass
    _BINANCE_SYMS_CACHE["set"] = syms
    _BINANCE_SYMS_CACHE["ts"] = now
    if syms:
        LOGGER.info("Loaded %d Binance futures symbols", len(syms))


# Aliases courants KuCoin → Binance
_ALIAS_BASE = {
    "XBT": "BTC",   # KuCoin XBT → Binance BTC
    "APR": "APE",   # Exemple d’alias
    # Ajoute ici si tu croises d’autres alias (ex: "BCH"->"BCH" inchangé)
}


@lru_cache(maxsize=2048)
def _map_to_binance_symbol(kucoin_symbol: str) -> Optional[str]:
    """
    Convertit un symbole KuCoin (ex: XBTUSDTM, ETHUSDTM) en symbole Binance (ex: BTCUSDT, ETHUSDT).
    Retourne None si mapping impossible ou si le symbole n’existe pas côté Binance.
    """
    if not kucoin_symbol:
        return None
    s = kucoin_symbol.upper().strip()

    # 1) Retire suffixes KuCoin futures courants
    for suf in ("USDTM", "USDT-PERP", "USDT-PERPP", "PERP", "M"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break

    # 2) Base + alias éventuel
    if s.endswith("USDT"):
        base = s[:-4]
    elif s.endswith("USDTM"):
        base = s[:-5]
    else:
        base = s
    base_alias = _ALIAS_BASE.get(base, base)

    # 3) Construit le symbole Binance et valide
    b_symbol = f"{base_alias}USDT"
    _refresh_binance_symbols()
    syms = _BINANCE_SYMS_CACHE["set"]

    if b_symbol in syms:
        return b_symbol

    # 4) Essai alternatif sans alias ou cas borderline
    alt = f"{base}USDT"
    if alt in syms:
        return alt

    _bump_and_maybe_warn("map", f"Binance symbol mapping failed for {kucoin_symbol}")
    return None


# --------------------------------------------------------------------
# 🔹 1. PROFIL VOLUME INSTITUTIONNEL (grands comptes vs. retail)
# --------------------------------------------------------------------
def get_large_trader_ratio(symbol: str) -> float:
    """
    Ratio 0..1 :
      - proche de 1 => flux dominé par gros traders
      - proche de 0 => flux retail
    Ne lève pas d’erreur : renvoie 0.5 (neutre) si indisponible.
    """
    b_sym = _map_to_binance_symbol(symbol)
    if not b_sym:
        _bump_and_maybe_warn("map", f"Mapping Binance introuvable pour {symbol}")
        return 0.5

    data = _safe_json_get(
        f"{BINANCE_FUTURES_DATA}/topLongShortAccountRatio",
        params={"symbol": b_sym, "period": "1h", "limit": 1},
        timeout=6.0,
        retries=2,
    )
    try:
        if isinstance(data, list) and data:
            row = data[0]
            # Champs: longAccount, shortAccount, longShortRatio, timestamp (strings)
            long_acc = float(row.get("longAccount", 0.0))
            short_acc = float(row.get("shortAccount", 0.0))
            if long_acc <= 0 and short_acc <= 0:
                return 0.5
            ratio = long_acc / max(1e-9, short_acc)
            # Écrase dans 0..1 via tanh-like
            score = np.tanh(ratio)
            return float(np.clip(score, 0.0, 1.0))
    except Exception as e:
        _bump_and_maybe_warn("large_ratio", f"Parse failed for {b_sym}", str(e))
    return 0.5


# --------------------------------------------------------------------
# 🔹 2. DELTA CUMULÉ PAR CANDLE (divergence CVD vs prix)
# --------------------------------------------------------------------
def get_cvd_divergence(symbol: str, limit: int = 500) -> float:
    """
    Score -1..+1 : cohérence CVD vs prix.
      +1 = prix & CVD montent ensemble
      -1 = divergence (prix ↑ mais CVD ↓, ou l’inverse)
    """
    b_sym = _map_to_binance_symbol(symbol)
    if not b_sym:
        _bump_and_maybe_warn("map", f"Mapping Binance introuvable pour {symbol}")
        return 0.0

    data = _safe_json_get(
        f"{BINANCE_FUTURES_API}/aggTrades",
        params={"symbol": b_sym, "limit": max(100, min(1000, int(limit)))},
        timeout=6.0,
        retries=2,
    )
    if not isinstance(data, list) or len(data) < 10:
        return 0.0

    try:
        df = pd.DataFrame(data)
        # Attendus: p (price), q (qty), m (isBuyerMaker)
        if not {"p", "q", "m"}.issubset(df.columns):
            return 0.0
        df["p"] = pd.to_numeric(df["p"], errors="coerce")
        df["q"] = pd.to_numeric(df["q"], errors="coerce")
        df = df.dropna(subset=["p", "q", "m"])
        if df.empty:
            return 0.0

        # maker côté vendeur => taker acheteur (m=False) ; code +1 pour taker acheteur
        df["side"] = df["m"].apply(lambda x: -1 if bool(x) else 1)
        df["delta"] = df["q"] * df["side"]

        cvd = float(df["delta"].sum())
        price_change = float(df["p"].iloc[-1] - df["p"].iloc[0])
        if abs(price_change) < 1e-10:
            return 0.0
        corr = np.sign(price_change) * np.sign(cvd)  # +1 cohérent, -1 divergence
        return float(np.clip(corr, -1.0, 1.0))
    except Exception as e:
        _bump_and_maybe_warn("cvd_div", f"CVD divergence parse failed for {b_sym}", str(e))
        return 0.0


# --------------------------------------------------------------------
# 🔹 3. LIQUIDITY MAP (equal highs/lows)
# --------------------------------------------------------------------
def detect_liquidity_clusters(df: pd.DataFrame, lookback: int = 50, tolerance: float = 0.0005):
    """
    Détecte des 'equal highs/lows' simples sur la fenêtre récente.
    Retourne: { "eq_highs": [...], "eq_lows": [...] }
    """
    try:
        highs = df["high"].astype(float).tail(lookback).values
        lows = df["low"].astype(float).tail(lookback).values
    except Exception:
        return {"eq_highs": [], "eq_lows": []}

    eq_highs, eq_lows = [], []
    for i in range(1, len(highs)):
        try:
            if abs(highs[i] - highs[i - 1]) / max(1e-12, highs[i]) < tolerance:
                eq_highs.append(highs[i])
            if abs(lows[i] - lows[i - 1]) / max(1e-12, lows[i]) < tolerance:
                eq_lows.append(lows[i])
        except Exception:
            continue

    return {
        "eq_highs": sorted({round(float(x), 6) for x in eq_highs}),
        "eq_lows": sorted({round(float(x), 6) for x in eq_lows}),
    }


# --------------------------------------------------------------------
# 🔸 SCORE INSTITUTIONNEL GLOBAL (pondéré)
# --------------------------------------------------------------------
def compute_institutional_score(symbol: str, bias: str, prev_oi: float = None) -> Dict[str, Any]:
    """
    Retourne:
    {
      "scores": {"oi": int, "fund": int, "cvd": int, "liquidity": int},
      "score_total": int,
      "details": {...}
    }
    - 'fund' est un placeholder (1) tant que le funding live n'est pas intégré.
    """
    large_ratio = get_large_trader_ratio(symbol)
    cvd_div = get_cvd_divergence(symbol)

    # Pondération simple (failsafe: neutres possibles)
    oi_score = 1 if large_ratio > 0.55 else 0
    cvd_score = 1 if cvd_div > 0 else 0
    fund_score = 1  # TODO: remplacer par un vrai funding si dispo
    liq_score = 0   # calcul côté structure si nécessaire

    score_total = oi_score + cvd_score + fund_score  # 0..3

    return {
        "scores": {
            "oi": oi_score,
            "fund": fund_score,
            "cvd": cvd_score,
            "liquidity": liq_score
        },
        "score_total": int(score_total),
        "details": {
            "large_ratio": round(float(large_ratio), 3),
            "cvd_divergence": float(cvd_div),
            "bias": str(bias).upper()
        }
    }


# --------------------------------------------------------------------
# 🔹 4. COUCHE D’ORCHESTRATION (pondération + commentaire)
# --------------------------------------------------------------------
def compute_full_institutional_analysis(symbol: str, bias: str, prev_oi: float = None) -> Dict[str, Any]:
    """
    Orchestration robuste:
    - Si mapping Binance indisponible → neutre (pas d'appel réseau)
    - Sinon calcule un score global et génère un commentaire
    """
    # Vérifie d'abord si le mapping est possible sans déclencher d'appels inutiles
    b_sym = _map_to_binance_symbol(symbol)
    if not b_sym:
        return {
            "institutional_score": 0,
            "institutional_strength": "Faible",
            "institutional_comment": "Pas de flux dominants",
            "neutral": True,
            "reason": "no_binance_mapping",
            "details": {}
        }

    inst = compute_institutional_score(symbol, bias, prev_oi)
    d = inst["scores"]
    total = int(inst["score_total"])

    comment = []
    if d.get("oi"):   comment.append("OI↑")
    if d.get("fund"): comment.append("Funding cohérent")
    if d.get("cvd"):  comment.append("CVD cohérent")
    strength = "Fort" if total == 3 else ("Moyen" if total == 2 else "Faible")

    return {
        "institutional_score": total,                 # utilisé par analyze_signal.py
        "institutional_strength": strength,
        "institutional_comment": ", ".join(comment) if comment else "Pas de flux dominants",
        "neutral": False,
        "details": inst
    }
