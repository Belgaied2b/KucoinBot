"""
institutional_data.py — Analyse institutionnelle avancée (robuste)
- Mapping KuCoin → Binance (XBTUSDTM → BTCUSDT, etc.)
- Cache des symboles Binance pour valider le mapping
- Fetch tolérant (JSON vide, rate-limit, réponses non-list)
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

LOGGER = logging.getLogger(__name__)

BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1"

# ==========
#  CACHES
# ==========
_BINANCE_SYMS_CACHE: Dict[str, Any] = {"ts": 0.0, "set": set()}
_BINANCE_SYMS_TTL = 15 * 60  # 15 minutes


def _safe_json_get(url: str, params: Optional[dict] = None, timeout: float = 7.0):
    try:
        r = requests.get(url, params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        LOGGER.debug("GET %s failed: %s", url, e)
        return None


def _refresh_binance_symbols():
    """Charge et met en cache la liste des symboles USDT futures (ex: BTCUSDT)."""
    now = time.time()
    if (now - _BINANCE_SYMS_CACHE["ts"]) < _BINANCE_SYMS_TTL and _BINANCE_SYMS_CACHE["set"]:
        return
    data = _safe_json_get(f"{BINANCE_FUTURES_API}/exchangeInfo")
    syms = set()
    # Structure attendue: {"symbols": [{"symbol":"BTCUSDT", "contractType":"PERPETUAL", ...}, ...]}
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


# Aliases courants KuCoin → Binance sur la base
_ALIAS_BASE = {
    "XBT": "BTC",   # KuCoin XBT → Binance BTC
    "APR": "APE",   # certains tickers "APR" sur KuCoin correspondent à APE sur Binance
}


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

    # 2) S'assure qu'on est en base+USDT
    if s.endswith("USDT"):
        base = s[:-4]
    else:
        # beaucoup de flux chez toi sont de type BASEUSDTM → force USDT
        if s.endswith("USDTM"):
            base = s[:-5]
        else:
            # parfois s est seulement le base
            base = s

    # 3) Alias base
    base_alias = _ALIAS_BASE.get(base, base)

    b_symbol = f"{base_alias}USDT"

    # 4) Valide contre la liste Binance
    _refresh_binance_symbols()
    syms = _BINANCE_SYMS_CACHE["set"]
    if b_symbol in syms:
        return b_symbol

    # 5) Dernier essai : si la base telle quelle existe déjà
    alt = f"{base}USDT"
    if alt in syms:
        return alt

    LOGGER.debug("Binance symbol mapping failed for %s → tried %s / %s", kucoin_symbol, b_symbol, alt)
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
        return 0.5
    data = _safe_json_get(
        f"{BINANCE_FUTURES_API}/topLongShortAccountRatio",
        params={"symbol": b_sym, "period": "1h", "limit": 1},
        timeout=6.0,
    )
    try:
        if isinstance(data, list) and data:
            row = data[0]
            # Champs typiques: longAccount, shortAccount, longShortRatio
            long_acc = float(row.get("longAccount", 0.0))
            short_acc = float(row.get("shortAccount", 0.0))
            if long_acc <= 0 and short_acc <= 0:
                return 0.5
            ratio = long_acc / max(1e-9, short_acc)
            score = np.tanh(ratio)  # 0..1
            return float(np.clip(score, 0.0, 1.0))
    except Exception as e:
        LOGGER.debug("Large trader ratio parse failed for %s: %s", b_sym, e)
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
        return 0.0

    data = _safe_json_get(
        f"{BINANCE_FUTURES_API}/aggTrades",
        params={"symbol": b_sym, "limit": max(100, min(1000, int(limit)))},
        timeout=6.0,
    )
    if not isinstance(data, list) or len(data) < 10:
        return 0.0

    try:
        df = pd.DataFrame(data)
        # attendus: p (price), q (qty), m (isBuyerMaker)
        if not {"p", "q", "m"}.issubset(df.columns):
            return 0.0
        df["p"] = pd.to_numeric(df["p"], errors="coerce")
        df["q"] = pd.to_numeric(df["q"], errors="coerce")
        df = df.dropna(subset=["p", "q", "m"])
        if df.empty:
            return 0.0

        # maker côté vendeur => taker acheteur (m=False) ; on code +1 pour taker acheteur
        df["side"] = df["m"].apply(lambda x: -1 if bool(x) else 1)
        df["delta"] = df["q"] * df["side"]

        cvd = float(df["delta"].sum())
        price_change = float(df["p"].iloc[-1] - df["p"].iloc[0])
        if abs(price_change) < 1e-10:
            return 0.0
        # signe cohérent ?
        corr = np.sign(price_change) * np.sign(cvd)
        return float(np.clip(corr, -1.0, 1.0))
    except Exception as e:
        LOGGER.debug("CVD divergence parse failed for %s: %s", b_sym, e)
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
def compute_institutional_score(symbol: str, bias: str, prev_oi: float = None):
    """
    Retourne:
    {
      "scores": {"oi": int, "fund": int, "cvd": int, "liquidity": int},
      "score_total": int,
      "details": {...}
    }
    - 'fund' est un placeholder (1) tant que tu n'intègres pas un vrai funding live.
    """
    large_ratio = get_large_trader_ratio(symbol)
    cvd_div = get_cvd_divergence(symbol)

    # Pondération simple (failsafe: neutres possibles)
    oi_score = 1 if large_ratio > 0.55 else 0
    cvd_score = 1 if cvd_div > 0 else 0
    fund_score = 1  # à remplacer si tu wires un vrai funding
    liq_score = 0   # calcul côté structure pour rester exact

    score_total = oi_score + cvd_score + fund_score

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
def compute_full_institutional_analysis(symbol: str, bias: str, prev_oi: float = None):
    inst = compute_institutional_score(symbol, bias, prev_oi)
    d = inst["scores"]; total = inst["score_total"]
    comment = []
    if d.get("oi"):   comment.append("OI↑")
    if d.get("fund"): comment.append("Funding cohérent")
    if d.get("cvd"):  comment.append("CVD cohérent")
    strength = "Fort" if total == 3 else ("Moyen" if total == 2 else "Faible")
    return {
        "institutional_score": total,
        "institutional_strength": strength,
        "institutional_comment": ", ".join(comment) if comment else "Pas de flux dominants",
        "details": inst
    }
