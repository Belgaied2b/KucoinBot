import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------------------
# Pivots & swings (HH / HL / LH / LL) — base structure moteur
# ---------------------------------------------------------------------


def _detect_pivots(df: pd.DataFrame, left: int = 2, right: int = 2, max_bars: int = 300) -> List[Dict[str, Any]]:
    """
    Détecte des pivots simples (fractal high/low) sur les dernières barres.
    Retourne une liste triée par temps:
      {"pos": int, "kind": "high"/"low", "price": float}
    """
    n = len(df)
    if n < left + right + 3:
        return []

    try:
        highs = df["high"].astype(float).to_numpy()
        lows = df["low"].astype(float).to_numpy()
    except Exception:
        return []

    start = max(left, n - max_bars)
    end = n - right
    pivots: List[Dict[str, Any]] = []

    for i in range(start, end):
        window_h = highs[i - left : i + right + 1]
        window_l = lows[i - left : i + right + 1]
        h = highs[i]
        l = lows[i]

        if h == float(window_h.max()):
            pivots.append({"pos": i, "kind": "high", "price": float(h)})
        if l == float(window_l.min()):
            pivots.append({"pos": i, "kind": "low", "price": float(l)})

    if not pivots:
        return []

    # tri croissant par index
    pivots.sort(key=lambda p: p["pos"])

    # compresse les pivot consécutifs de même type en conservant l'extrême
    compressed: List[Dict[str, Any]] = []
    for p in pivots:
        if not compressed:
            compressed.append(p)
            continue
        last = compressed[-1]
        if p["kind"] != last["kind"]:
            compressed.append(p)
        else:
            # même type : garde l'extrême "le plus loin"
            if p["kind"] == "high":
                if p["price"] >= last["price"]:
                    compressed[-1] = p
            else:
                if p["price"] <= last["price"]:
                    compressed[-1] = p

    return compressed[-max_bars:]


def _build_swings(df: pd.DataFrame, left: int = 2, right: int = 2, max_pivots: int = 50) -> List[Dict[str, Any]]:
    """
    Construit une séquence de swings labellisés:
      - HIGH: HH (higher high) / LH (lower high) / H (premier)
      - LOW:  HL (higher low)  / LL (lower low) / L (premier)
    """
    pivots = _detect_pivots(df, left=left, right=right, max_bars=max_pivots * 3)
    if not pivots:
        return []

    swings: List[Dict[str, Any]] = []
    last_high: Optional[float] = None
    last_low: Optional[float] = None

    for p in pivots[-max_pivots:]:
        label = None
        if p["kind"] == "high":
            if last_high is None:
                label = "H"
            else:
                label = "HH" if p["price"] > last_high else "LH"
            last_high = p["price"]
        else:
            if last_low is None:
                label = "L"
            else:
                label = "HL" if p["price"] > last_low else "LL"
            last_low = p["price"]

        swings.append(
            {
                "pos": int(p["pos"]),
                "kind": p["kind"],
                "price": float(p["price"]),
                "label": label,
            }
        )

    return swings


def _trend_from_labels(labels: List[str]) -> str:
    """
    Estime un trend simple à partir d'une liste de labels HH/HL/LH/LL.
      - 'up'    si prédominance HH+HL et peu de LL
      - 'down'  si prédominance LL+LH et peu de HH
      - 'range' sinon
    """
    if not labels:
        return "unknown"

    from collections import Counter

    c = Counter([x for x in labels if x])
    hh = c.get("HH", 0)
    hl = c.get("HL", 0)
    lh = c.get("LH", 0)
    ll = c.get("LL", 0)

    up_score = hh + hl
    down_score = ll + lh

    if up_score >= 2 and ll == 0 and up_score >= down_score:
        return "up"
    if down_score >= 2 and hh == 0 and down_score >= up_score:
        return "down"
    if up_score == 0 and down_score == 0:
        return "unknown"
    return "range"


def analyze_structure(df: pd.DataFrame, bias: Optional[str] = None,
                      left: int = 2, right: int = 2, max_pivots: int = 50) -> Dict[str, Any]:
    """
    Analyse de structure de marché à partir des swings:
      - swings: liste des derniers swings HH/HL/LH/LL
      - bos_direction: 'UP' / 'DOWN' si close casse dernier swing high/low
      - choch_direction: 'UP' / 'DOWN' si changement de trend up<->down
      - trend_state: 'up' / 'down' / 'range' / 'unknown'
      - phase: 'expansion' / 'pullback' / 'distribution' / 'accumulation' / 'unknown'
      - cos: 'trend_to_range' / 'range_to_trend' / None
      - last_event: description textuelle du dernier évènement structurel
    """
    out: Dict[str, Any] = {
        "swings": [],
        "bos_direction": None,
        "choch_direction": None,
        "trend_state": "unknown",
        "phase": "unknown",
        "cos": None,
        "last_event": None,
    }
    if df is None or len(df) < 10:
        return out

    swings = _build_swings(df, left=left, right=right, max_pivots=max_pivots)
    out["swings"] = swings
    if not swings:
        return out

    close = float(df["close"].iloc[-1])

    # dernier swing high / low (avant la dernière bougie)
    last_high = None
    last_low = None
    for s in swings:
        if s["pos"] >= len(df) - 1:
            continue
        if s["kind"] == "high":
            if last_high is None or s["pos"] >= last_high["pos"]:
                last_high = s
        else:
            if last_low is None or s["pos"] >= last_low["pos"]:
                last_low = s

    bos_dir = None
    if last_high is not None and close > float(last_high["price"]):
        bos_dir = "UP"
    elif last_low is not None and close < float(last_low["price"]):
        bos_dir = "DOWN"
    out["bos_direction"] = bos_dir

    # trend global à partir des labels
    labels = [s.get("label") for s in swings if s.get("label")]
    trend = _trend_from_labels(labels)
    out["trend_state"] = trend

    # trend précédent (sans les 2 derniers swings) pour détecter CHoCH/COS
    prev_trend = _trend_from_labels(labels[:-2]) if len(labels) >= 4 else "unknown"

    choch = None
    cos = None
    last_event = None

    if prev_trend in ("up", "down") and trend in ("up", "down") and prev_trend != trend:
        # vrai CHoCH (trend haussier -> baissier ou inverse)
        choch = "UP" if trend == "up" else "DOWN"
        last_event = f"choch_{trend}"
    elif prev_trend in ("up", "down") and trend == "range":
        cos = "trend_to_range"
        last_event = "cos_trend_to_range"
    elif prev_trend == "range" and trend in ("up", "down"):
        cos = "range_to_trend"
        last_event = "cos_range_to_trend"
    elif bos_dir is not None:
        last_event = f"bos_{bos_dir.lower()}"

    out["choch_direction"] = choch
    out["cos"] = cos
    out["last_event"] = last_event

    # phase de structure (grossière)
    phase = "unknown"
    if trend == "up":
        phase = "expansion" if bos_dir == "UP" else "pullback"
    elif trend == "down":
        phase = "expansion" if bos_dir == "DOWN" else "pullback"
    elif trend == "range":
        if prev_trend == "up":
            phase = "distribution"
        elif prev_trend == "down":
            phase = "accumulation"

    out["phase"] = phase
    return out


# === Interface de base : BOS / Validation structurelle ===


def detect_bos(df: pd.DataFrame, lookback: int = 10):
    """
    Compatibilité: renvoie 'BOS_UP' / 'BOS_DOWN' ou None à partir de analyze_structure.
    """
    ctx = analyze_structure(df)
    if ctx.get("bos_direction") == "UP":
        return "BOS_UP"
    if ctx.get("bos_direction") == "DOWN":
        return "BOS_DOWN"
    return None


def structure_valid(df: pd.DataFrame, bias: str, lookback: int = 10) -> bool:
    """
    Validation simple de structure:
      - Pour LONG: BOS_UP récent OU trend_state 'up'
      - Pour SHORT: BOS_DOWN récent OU trend_state 'down'
    """
    if df is None or len(df) < max(5, lookback):
        return True

    ctx = analyze_structure(df, bias)
    bos = ctx.get("bos_direction")
    trend = ctx.get("trend_state")

    b = str(bias or "").upper()
    if b == "LONG":
        return bool(bos == "UP" or trend == "up")
    if b == "SHORT":
        return bool(bos == "DOWN" or trend == "down")
    return True


# === HTF trend (EMA) ===


def _ema(x: pd.Series, n: int = 20) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def htf_trend_ok(df_htf: Optional[pd.DataFrame], bias: str) -> bool:
    """
    Vérifie la tendance en H4 via EMA20/50.
    True si:
      - LONG: close > EMA50 et EMA20 > EMA50
      - SHORT: close < EMA50 et EMA20 < EMA50
    Si df_htf manquant ou trop court -> True (ne bloque pas).
    """
    if df_htf is None or len(df_htf) < 60:
        return True
    close = df_htf["close"].astype(float)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    if str(bias).upper() == "LONG":
        return bool(close.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1])
    return bool(close.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-1] < ema50.iloc[-1])


# === Qualité de break BOS: volume + OI (+liquidité optionnelle) ===

try:
    # on réutilise la fonction déjà définie dans institutional_data.py
    from institutional_data import detect_liquidity_clusters
except Exception:  # pragma: no cover
    detect_liquidity_clusters = None  # type: ignore


def bos_quality_details(
    df: pd.DataFrame,
    oi_series: Optional[pd.Series] = None,
    vol_lookback: int = 60,
    vol_pct: float = 0.80,
    oi_min_trend: float = 0.003,
    oi_min_squeeze: float = -0.005,
    df_liq: Optional[pd.DataFrame] = None,
    price: Optional[float] = None,
    tick: float = 0.0,
) -> Dict[str, Any]:
    """
    Renvoie un dict riche décrivant la qualité du break:
      {
        "ok": bool,
        "vol_ok": bool,
        "oi_ok": bool,
        "bos_direction": "UP"/"DOWN"/None,
        "has_liquidity_zone": bool,
        "liquidity_side": "UP"/"DOWN"/None,
        "liq_distance": float or None,
        "liq_distance_bps": float or None,
      }
    """
    out: Dict[str, Any] = {
        "ok": True,
        "vol_ok": True,
        "oi_ok": True,
        "bos_direction": None,
        "has_liquidity_zone": False,
        "liquidity_side": None,
        "liq_distance": None,
        "liq_distance_bps": None,
    }

    if df is None or len(df) < max(5, vol_lookback):
        return out

    # direction BOS via structure
    ctx = analyze_structure(df)
    out["bos_direction"] = ctx.get("bos_direction")

    # --- Volume ---
    try:
        vol = df["volume"].astype(float).tail(vol_lookback)
        v_last = float(vol.iloc[-1])
        thresh = float(vol.quantile(vol_pct))
        vol_ok = v_last >= thresh
    except Exception:
        vol_ok = True
    out["vol_ok"] = bool(vol_ok)

    # --- Open interest ---
    oi_ok = True
    if oi_series is not None and len(oi_series) >= 3:
        try:
            o = oi_series.astype(float).tail(3)
            pct = (o.iloc[-1] - o.iloc[0]) / max(1e-12, o.iloc[0])
            if pct >= oi_min_trend:
                oi_ok = True  # trend building
            elif pct <= oi_min_squeeze:
                oi_ok = True  # squeeze/delever, acceptable
            else:
                oi_ok = False
        except Exception:
            oi_ok = True
    out["oi_ok"] = bool(oi_ok)
    out["ok"] = bool(vol_ok and oi_ok)

    # --- LIQUIDITÉ (equal highs/lows) ---
    ref_price = float(price) if price is not None else float(df["close"].iloc[-1])
    tick = float(tick or 0.0)

    eq_highs: List[float] = []
    eq_lows: List[float] = []

    try:
        base_df = df_liq if isinstance(df_liq, pd.DataFrame) else df
        if detect_liquidity_clusters is not None and base_df is not None:
            liq = detect_liquidity_clusters(base_df, lookback=80, tolerance=0.0005)
            # 🔒 protection: on ne fait .get que si c'est bien un dict
            if isinstance(liq, dict):
                eq_highs = [float(x) for x in liq.get("eq_highs", [])]
                eq_lows = [float(x) for x in liq.get("eq_lows", [])]
    except Exception:
        eq_highs = []
        eq_lows = []

    if eq_highs or eq_lows:
        out["has_liquidity_zone"] = True
        all_lvls = [(abs(h - ref_price), h, "UP") for h in eq_highs] + \
                   [(abs(l - ref_price), l, "DOWN") for l in eq_lows]
        all_lvls.sort(key=lambda x: x[0])
        if all_lvls:
            dist, lvl, side = all_lvls[0]
            out["liquidity_side"] = side
            out["liq_distance"] = float(dist)
            bps = (dist / max(abs(ref_price), 1e-12)) * 10000.0  # basis points
            out["liq_distance_bps"] = float(bps)

    return out


def bos_quality_ok(
    df: pd.DataFrame,
    oi_series: Optional[pd.Series] = None,
    vol_lookback: int = 60,
    vol_pct: float = 0.80,
    oi_min_trend: float = 0.003,
    oi_min_squeeze: float = -0.005,
) -> bool:
    """
    Wrapper compat: conserve l'ancienne signature en renvoyant juste le booléen.
    """
    d = bos_quality_details(
        df=df,
        oi_series=oi_series,
        vol_lookback=vol_lookback,
        vol_pct=vol_pct,
        oi_min_trend=oi_min_trend,
        oi_min_squeeze=oi_min_squeeze,
    )
    return bool(d.get("ok", True))


# === Commitment score (OI + CVD) ===


def _slope(series: pd.Series, window: int = 10) -> float:
    if len(series) < window:
        return 0.0
    y = series.tail(window).astype(float).values
    x = np.arange(len(y))
    n = len(y)
    denom = (n * (x ** 2).sum() - x.sum() ** 2) or 1.0
    a = (n * (x * y).sum() - x.sum() * y.sum()) / denom
    return float(a)


def commitment_score(
    oi_series: Optional[pd.Series],
    cvd_series: Optional[pd.Series],
    window: int = 30,
) -> float:
    """
    Combine OI (engagement) + CVD (agressivité nette) en un score 0..1.
    Normalisation robuste par MAD; clampé à [0, 1].
    """
    oi_comp = 0.0
    if oi_series is not None and len(oi_series) >= 3:
        o = oi_series.astype(float).tail(window)
        try:
            pct = (o.iloc[-1] - o.iloc[0]) / max(1e-12, o.iloc[0])
            oi_comp = float(pct)
        except Exception:
            oi_comp = 0.0

    cvd_comp = 0.0
    if cvd_series is not None and len(cvd_series) >= window:
        c = cvd_series.astype(float).tail(window)
        m = _slope(c, window=window)
        mad = np.median(np.abs(c - np.median(c))) + 1e-12
        cvd_comp = float(m / mad)

    raw = 0.6 * oi_comp + 0.4 * cvd_comp
    score = 1.0 / (1.0 + np.exp(-3.5 * raw))  # logistic
    return float(np.clip(score, 0.0, 1.0))
