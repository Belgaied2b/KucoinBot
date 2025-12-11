# =====================================================================
# indicators.py — Institutional++ Technical & Momentum Suite
# =====================================================================
# Objectifs :
#   - Fournir les briques techniques de base (EMA, RSI, MACD, ATR, OTE)
#   - Ajouter une couche "desk" institutionnelle :
#       * momentum composite
#       * signal d'extension / mean-reversion
#       * régimes de volatilité
#   - Rester 100% compatible avec le reste du bot :
#       * rsi, macd, ema, true_atr, compute_ote, volatility_regime,
#         institutional_momentum, detect_rsi_divergence
# =====================================================================

from typing import Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd


# =====================================================================
# SAFE HELPERS
# =====================================================================

def _safe_len(obj) -> int:
    try:
        return len(obj)
    except Exception:
        return 0


def _safe_series(series, fill: float = 0.0) -> pd.Series:
    if series is None:
        return pd.Series([fill])
    try:
        s = pd.Series(series, dtype="float64")
    except Exception:
        s = pd.Series([fill])
    return s


# =====================================================================
# EMA / SMA
# =====================================================================

def ema(series: pd.Series, length: int) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) < 2:
        return pd.Series([np.nan])
    return s.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) < length:
        return pd.Series([np.nan] * _safe_len(s))
    return s.rolling(window=length).mean()


# =====================================================================
# RSI
# =====================================================================

def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """
    RSI style Wilder, robuste et stable même sur peu de données.
    """
    s = _safe_series(series)
    n = _safe_len(s)
    if n <= length:
        # on renvoie un RSI neutre quand on a peu d'historique
        return pd.Series([50.0] * n, index=s.index)

    delta = s.diff()

    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Moyenne exponentielle à la Wilder
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    rsi_val = 100.0 - (100.0 / (1.0 + rs))

    return rsi_val.fillna(50.0)


# =====================================================================
# MACD
# =====================================================================

def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD standard (12/26/9) retournant :
      - macd_line
      - signal_line
      - histogram (macd_line - signal_line)
    """
    s = _safe_series(series)
    if _safe_len(s) < slow + signal:
        nan = pd.Series([np.nan] * _safe_len(s), index=s.index)
        return nan, nan, nan

    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line

    return macd_line, signal_line, hist


# =====================================================================
# TRUE ATR
# =====================================================================

def true_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """
    Average True Range (Wilder) sur length périodes.
    Utilisé pour SL institutionnel, RR, régimes de volatilité, etc.
    """
    if df is None or _safe_len(df) < 2:
        return pd.Series([0.0])

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / length, adjust=False).mean()
    return atr.fillna(method="bfill").fillna(0.0)


# =====================================================================
# VOLATILITY REGIME
# =====================================================================

def volatility_regime(df: pd.DataFrame, length: int = 14) -> str:
    """
    Classe le marché en régime de volatilité :
      - "LOW"    : ATR% < 1 %
      - "MEDIUM" : 1 % <= ATR% < 3 %
      - "HIGH"   : ATR% >= 3 %
    """
    if df is None or _safe_len(df) < length + 2:
        return "UNKNOWN"

    atr = true_atr(df, length=length)
    last_atr = float(atr.iloc[-1])
    last_price = float(df["close"].iloc[-1])

    if last_price <= 0:
        return "UNKNOWN"

    atr_pct = last_atr / last_price

    if atr_pct < 0.01:
        return "LOW"
    if atr_pct < 0.03:
        return "MEDIUM"
    return "HIGH"


# =====================================================================
# OTE (Optimal Trade Entry) — Value zones
# =====================================================================

def compute_ote(df: pd.DataFrame, lookback: int = 80) -> Dict[str, Any]:
    """
    Approximation OTE sur la dernière jambe swing :
      - discount zone ~ zone d'achat idéale
      - premium zone  ~ zone de vente idéale

    Retourne :
      {
        "discount_zone": (low_ote, high_ote),
        "premium_zone": (low_ote, high_ote),
        "in_discount": bool,
        "in_premium": bool,
      }
    """
    if df is None or _safe_len(df) < lookback:
        return {
            "discount_zone": (None, None),
            "premium_zone": (None, None),
            "in_discount": False,
            "in_premium": False,
        }

    w = df.tail(lookback)
    high = float(w["high"].max())
    low = float(w["low"].min())
    last = float(w["close"].iloc[-1])

    if high <= low:
        return {
            "discount_zone": (None, None),
            "premium_zone": (None, None),
            "in_discount": False,
            "in_premium": False,
        }

    diff = high - low

    # Zones fibo classiques ICT-ish
    discount_low = low + 0.62 * diff
    discount_high = low + 0.79 * diff

    premium_low = low + 0.20 * diff
    premium_high = low + 0.38 * diff

    in_discount = discount_low <= last <= discount_high
    in_premium = premium_low <= last <= premium_high

    return {
        "discount_zone": (discount_low, discount_high),
        "premium_zone": (premium_low, premium_high),
        "in_discount": bool(in_discount),
        "in_premium": bool(in_premium),
    }


# =====================================================================
# EXTENSION / MEAN REVERSION SIGNAL
# =====================================================================

def extension_signal(
    df: pd.DataFrame,
    ema_fast_len: int = 20,
    ema_slow_len: int = 50,
    atr_len: int = 14,
    k: float = 1.5,
) -> str:
    """
    Détecte une situation d'over-extension par rapport aux EMA + ATR.

    Idée :
      - On regarde la distance du prix actuel aux EMA20/50,
        normalisée par l'ATR.
      - Si c'est trop étendu dans un sens, on est en zone "mean reversion".

    Retourne :
      - "OVEREXTENDED_LONG"
      - "OVEREXTENDED_SHORT"
      - "NORMAL"
    """
    if df is None or _safe_len(df) < max(ema_slow_len, atr_len) + 5:
        return "NORMAL"

    close = df["close"].astype(float)
    ema_fast = ema(close, ema_fast_len)
    ema_slow = ema(close, ema_slow_len)
    atr = true_atr(df, length=atr_len)

    last_price = float(close.iloc[-1])
    last_ema = float(ema_fast.iloc[-1])
    last_ema_slow = float(ema_slow.iloc[-1])
    last_atr = float(atr.iloc[-1])

    if last_atr <= 0:
        return "NORMAL"

    # Distance à l'EMA20 et EMA50
    dist_fast = (last_price - last_ema) / last_atr
    dist_slow = (last_price - last_ema_slow) / last_atr

    # Si les deux sont dans le même sens et assez forts, on considère une extension
    if dist_fast > k and dist_slow > k:
        return "OVEREXTENDED_LONG"
    if dist_fast < -k and dist_slow < -k:
        return "OVEREXTENDED_SHORT"

    return "NORMAL"


# =====================================================================
# MOMENTUM INSTITUTIONNEL & COMPOSITE
# =====================================================================

def institutional_momentum(df: pd.DataFrame) -> str:
    """
    Version label simple (BACKWARD COMPATIBLE avec analyze_signal.py) :

      - "STRONG_BULLISH"
      - "BULLISH"
      - "STRONG_BEARISH"
      - "BEARISH"
      - "NEUTRAL"

    Basé sur :
      - MACD (signe + pente)
      - EMA20/EMA50 (cross + pente)
      - RSI
      - Volume spike
    """
    if df is None or _safe_len(df) < 40:
        return "NEUTRAL"

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    r = rsi(close, length=14)
    macd_line, signal_line, hist = macd(close)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)

    if _safe_len(macd_line) < 5:
        return "NEUTRAL"

    macd_slope = float(macd_line.iloc[-1] - macd_line.iloc[-5])

    # Volume spike sur les 20 dernières barres
    if _safe_len(volume) >= 20:
        avg_vol = float(volume.iloc[-20:].mean())
        vol_spike = float(volume.iloc[-1]) > 1.8 * avg_vol
    else:
        vol_spike = False

    last_rsi = float(r.iloc[-1])
    last_hist = float(hist.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])

    # Forte tendance haussière
    if (
        last_hist > 0
        and macd_slope > 0
        and last_ema20 > last_ema50
        and last_rsi > 55
    ):
        return "STRONG_BULLISH" if not vol_spike else "BULLISH"

    # Tendance haussière modérée
    if (
        last_hist > 0
        and last_ema20 > last_ema50
        and last_rsi > 50
    ):
        return "BULLISH"

    # Forte tendance baissière
    if (
        last_hist < 0
        and macd_slope < 0
        and last_ema20 < last_ema50
        and last_rsi < 45
    ):
        return "STRONG_BEARISH" if not vol_spike else "BEARISH"

    # Tendance baissière modérée
    if (
        last_hist < 0
        and last_ema20 < last_ema50
        and last_rsi < 50
    ):
        return "BEARISH"

    return "NEUTRAL"


def composite_momentum(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Version "desk lead++" : momentum composite sur 0–100.

    Composants :
      - Trend EMA20/EMA50 (direction + pente)
      - MACD (signe + pente)
      - RSI (distance à 50)
      - Extension/Mean Reversion (via extension_signal)
      - Volume (spike ou non)

    Retourne :
      {
        "score": float 0–100,
        "label": "VERY_BULLISH" / "BULLISH" /
                 "NEUTRAL" / "BEARISH" / "VERY_BEARISH",
        "components": { ... },
      }
    """
    if df is None or _safe_len(df) < 40:
        return {"score": 50.0, "label": "NEUTRAL", "components": {}}

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    # Base components
    r = rsi(close, length=14)
    macd_line, signal_line, hist = macd(close)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)

    if _safe_len(macd_line) < 5:
        return {"score": 50.0, "label": "NEUTRAL", "components": {}}

    macd_slope = float(macd_line.iloc[-1] - macd_line.iloc[-5])
    last_rsi = float(r.iloc[-1])
    last_hist = float(hist.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])

    # Trend score (EMA)
    if last_ema20 > last_ema50:
        trend_score = 1.0
    elif last_ema20 < last_ema50:
        trend_score = -1.0
    else:
        trend_score = 0.0

    # MACD score
    macd_score = np.tanh(last_hist * 5.0) + np.tanh(macd_slope * 10.0)
    macd_score = float(macd_score)  # ~[-2, 2]

    # RSI score (distance à 50)
    rsi_score = (last_rsi - 50.0) / 25.0  # ~[-2, 2] pour RSI dans [0,100]

    # Volume score
    if _safe_len(volume) >= 20:
        avg_vol = float(volume.iloc[-20:].mean())
        vol_ratio = float(volume.iloc[-1]) / (avg_vol + 1e-8)
        vol_score = np.tanh((vol_ratio - 1.0) * 2.0)  # [-1,1]
    else:
        vol_score = 0.0

    # Extension signal
    ext = extension_signal(df)
    if ext == "OVEREXTENDED_LONG":
        ext_score = -0.7  # trop étiré à l'achat => risque de mean reversion
    elif ext == "OVEREXTENDED_SHORT":
        ext_score = 0.7   # trop étiré à la vente
    else:
        ext_score = 0.0

    # Combine the components
    raw = (
        0.4 * trend_score +        # direction structurelle
        0.4 * (macd_score / 2.0) + # normalise macd_score [-1,1]
        0.3 * (rsi_score / 2.0) +  # normalise rsi_score [-1,1]
        0.2 * vol_score +          # volume
        0.3 * ext_score            # extension / mean revert
    )

    # raw ~ [-2, 2] environ → on mappe en [0, 100]
    score = float(50.0 + 25.0 * raw)
    score = max(0.0, min(100.0, score))

    # Label discret
    if score >= 75:
        label = "VERY_BULLISH"
    elif score >= 60:
        label = "BULLISH"
    elif score <= 25:
        label = "VERY_BEARISH"
    elif score <= 40:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    return {
        "score": score,
        "label": label,
        "components": {
            "trend_score": float(trend_score),
            "macd_score": float(macd_score),
            "rsi_score": float(rsi_score),
            "vol_score": float(vol_score),
            "ext_score": float(ext_score),
        },
    }


# =====================================================================
# RSI DIVERGENCE SIMPLE
# =====================================================================

def detect_rsi_divergence(df: pd.DataFrame) -> Optional[str]:
    """
    Détection simple de divergence RSI sur ~5–10 barres.

    Retourne :
      - "bullish"  : prix fait un plus bas plus bas, RSI un plus bas plus haut
      - "bearish"  : prix fait un plus haut plus haut, RSI un plus haut plus bas
      - None       : pas de divergence claire
    """
    if df is None or _safe_len(df) < 15:
        return None

    close = df["close"].astype(float)
    r = rsi(close, length=14)

    # On regarde un point "récent" et un point "ancien"
    p1 = float(close.iloc[-1])
    p2 = float(close.iloc[-6])
    r1 = float(r.iloc[-1])
    r2 = float(r.iloc[-6])

    # Divergence haussière
    if p1 < p2 and r1 > r2:
        return "bullish"

    # Divergence baissière
    if p1 > p2 and r1 < r2:
        return "bearish"

    return None
