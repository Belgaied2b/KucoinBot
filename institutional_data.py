import time
import httpx
from typing import Optional

from logger_utils import get_logger

# --------------------------------------------------------------------------------------
# Logger: on loggue via logger; on ne tombe sur print QUE si le logger échoue.
# --------------------------------------------------------------------------------------
try:
    _logger = get_logger("institutional_data")
except Exception:
    _logger = None  # fallback -> print

def _log_info(msg: str):
    if _logger:
        try:
            _logger.info(msg)
            return
        except Exception:
            pass
    print(msg, flush=True)

def _log_warn(msg: str):
    if _logger:
        try:
            _logger.warning(msg)
            return
        except Exception:
            pass
    print(msg, flush=True)

def _log_exc(prefix: str, e: Exception):
    if _logger:
        try:
            _logger.exception(f"{prefix} error: {e}")
            return
        except Exception:
            pass
    print(f"{prefix} error: {e}", flush=True)

# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
BASE = "https://fapi.binance.com"  # Binance Futures (USDT-M)
CG   = "https://api.coingecko.com/api/v3"

def _get(url: str, params: Optional[dict] = None, timeout: float = 6.0) -> httpx.Response:
    return httpx.get(url, params=params or {}, timeout=timeout, headers={"Accept": "application/json"})

# --------------------------------------------------------------------------------------
# Utils
# --------------------------------------------------------------------------------------
def map_symbol_to_binance(sym: str) -> str:
    s = (sym or "").upper()
    if s.endswith("USDTM"):
        s = s.replace("USDTM", "USDT")
    if s.endswith(".P"):
        s = s.replace(".P", "")
    return s

# --------------------------------------------------------------------------------------
# Funding / OI (valeurs brutes)
# --------------------------------------------------------------------------------------
def get_funding_rate(symbol: str) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = _get(f"{BASE}/fapi/v1/premiumIndex", {"symbol": b_symbol}, timeout=6.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[Funding] {b_symbol} data={data} ({ms:.1f} ms)")
            return float(data.get("lastFundingRate", 0.0) or 0.0)
        _log_warn(f"[Funding] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc(f"[Funding] {b_symbol}", e)
    return 0.0

def get_open_interest(symbol: str) -> float:
    """
    OI instantané (contrats) — endpoint public.
    GET /fapi/v1/openInterest -> {"openInterest":"12345.678","symbol":"BTCUSDT","time":...}
    """
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = _get(f"{BASE}/fapi/v1/openInterest", {"symbol": b_symbol}, timeout=6.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[OI] {b_symbol} data={data} ({ms:.1f} ms)")
            return float(data.get("openInterest", 0.0) or 0.0)
        _log_warn(f"[OI] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc(f"[OI] {b_symbol}", e)
    return 0.0

# --------------------------------------------------------------------------------------
# Liq — Legacy + Proxy
# --------------------------------------------------------------------------------------
def _mark_price(symbol_binance: str) -> float:
    """Mark price helper (for the liq proxy)."""
    try:
        r = _get(f"{BASE}/fapi/v1/premiumIndex", {"symbol": symbol_binance}, timeout=5.0)
        if r.status_code == 200:
            return float((r.json().get("markPrice")) or 0.0)
    except Exception:
        pass
    return 0.0

def get_recent_liquidations(symbol: str, minutes: int = 5) -> float:
    """
    Liquidations récentes (notionnel approx).
    1) (optionnel) Endpoint legacy /fapi/v1/allForceOrders — souvent 400 désormais
    2) Fallback PROXY via taker long/short ratio 5m * markPrice

    Proxy = |buyVol - sellVol| * markPrice  (aligne tes logs: notionnel≈... )
    """
    b_symbol = map_symbol_to_binance(symbol)

    # Flag pour contrôler l'usage de l'endpoint legacy (déconseillé)
    try:
        from config import SETTINGS
        use_legacy = bool(getattr(SETTINGS, "use_legacy_binance_liq", False))
    except Exception:
        use_legacy = False

    # --- Try legacy allForceOrders (souvent 400 "out of maintenance") ---
    if use_legacy:
        try:
            now = int(time.time() * 1000)
            start = now - minutes * 60 * 1000
            t0 = time.time()
            r = _get(
                f"{BASE}/fapi/v1/allForceOrders",
                {"symbol": b_symbol, "startTime": start, "limit": 1000},
                timeout=6.0,
            )
            ms = (time.time() - t0) * 1000
            if r.status_code == 200:
                data = r.json()
                tot = 0.0
                for it in data:
                    try:
                        qty = float(it.get("origQty", 0.0) or 0.0)
                        px  = float(it.get("price",   0.0) or 0.0)
                        tot += qty * px
                    except Exception:
                        continue
                _log_info(f"[Liq] {b_symbol} {len(data)} orders, notionnel≈{tot:.2f} ({ms:.1f} ms)")
                return tot
            else:
                # 400 -> ne pas spammer en WARNING
                if r.status_code == 400:
                    _log_info(f"[Liq] {b_symbol} HTTP {r.status_code} resp={r.text}")
                else:
                    _log_warn(f"[Liq] {b_symbol} HTTP {r.status_code} resp={r.text}")
        except Exception as e:
            _log_exc(f"[Liq] {b_symbol}", e)

    # --- Fallback PROXY via takerLongShortRatio (5m) ---
    try:
        t0 = time.time()
        rr = _get(
            f"{BASE}/futures/data/takerlongshortRatio",
            {"symbol": b_symbol, "period": "5m", "limit": 1},
            timeout=6.0,
        )
        ms = (time.time() - t0) * 1000
        if rr.status_code == 200:
            arr = rr.json() or []
            if arr:
                rec = arr[-1]
                buy_vol  = float(rec.get("buyVol",  0.0) or 0.0)
                sell_vol = float(rec.get("sellVol", 0.0) or 0.0)
                # Proxy notionnel = imbalance * mark
                imb_abs  = abs(buy_vol - sell_vol)
                px       = _mark_price(b_symbol)
                proxy    = imb_abs * (px if px > 0 else 1.0)
                _log_info(
                    f"[Liq-PROXY] {b_symbol} buyVol={buy_vol} sellVol={sell_vol} "
                    f"mark={px} -> notionnel≈{proxy:.2f} ({ms:.1f} ms)"
                )
                return proxy
        _log_warn(f"[Liq-PROXY] {b_symbol} HTTP {rr.status_code} resp={rr.text}")
    except Exception as e:
        _log_exc(f"[Liq-PROXY] {b_symbol}", e)

    return 0.0

# --------------------------------------------------------------------------------------
# LIQ PACK -> transforme le proxy en liq_new_score [0..1] + meta
# --------------------------------------------------------------------------------------
try:
    from config import SETTINGS
except Exception:
    # Defaults si SETTINGS indisponible (tests locaux)
    SETTINGS = type("S", (), {})()
    setattr(SETTINGS, "use_legacy_binance_liq", False)
    setattr(SETTINGS, "liq_notional_norm", 150_000.0)  # norme globale
    setattr(SETTINGS, "liq_notional_overrides", {})    # ex: {"BTCUSDT": 3_000_000.0}
    setattr(SETTINGS, "liq_imbal_weight", 0.35)        # poids imbalance

def _norm_for_symbol(symbol_binance: str) -> float:
    """Norme de notional pour le scoring; override par symbole sinon défaut global."""
    try:
        overrides = getattr(SETTINGS, "liq_notional_overrides", {}) or {}
        if symbol_binance in overrides:
            return float(overrides[symbol_binance])
    except Exception:
        pass
    try:
        return float(getattr(SETTINGS, "liq_notional_norm", 150_000.0))
    except Exception:
        return 150_000.0

def _score_from_proxy(buy_vol: float, sell_vol: float, mark: float, symbol_binance: str):
    """
    Score borné [0..1] basé sur:
      - notionnel = |buyVol - sellVol| * mark
      - base = min(1, notionnel / norm)
      - imbalance = |buyVol - sellVol| / max(1, buyVol + sellVol)
      - score = (1-w)*base + w*imbalance
    """
    buy_vol = float(buy_vol or 0.0)
    sell_vol = float(sell_vol or 0.0)
    mark     = float(mark or 0.0)

    imb_abs = abs(buy_vol - sell_vol)
    denom   = max(1.0, buy_vol + sell_vol)
    imb     = imb_abs / denom

    notional = imb_abs * (mark if mark > 0 else 1.0)
    norm = _norm_for_symbol(symbol_binance)

    base = min(1.0, notional / max(1.0, norm))
    try:
        w = float(getattr(SETTINGS, "liq_imbal_weight", 0.35))
    except Exception:
        w = 0.35

    score = min(1.0, max(0.0, (1.0 - w) * base + w * imb))
    return float(score), float(notional), float(imb)

def get_liq_pack(symbol: str) -> dict:
    """
    Retourne un dict prêt à merger dans 'inst':
      {
        "liq_new_score": float [0..1],
        "liq_score": float [0..1],          # compat rétro
        "liq_notional_5m": float,
        "liq_imbalance_5m": float,
        "liq_source": "primary" | "proxy" | "none"
      }
    """
    b_symbol = map_symbol_to_binance(symbol)

    # 1) Essai legacy allForceOrders (optionnel, souvent 400)
    try:
        use_legacy = bool(getattr(SETTINGS, "use_legacy_binance_liq", False))
    except Exception:
        use_legacy = False

    if use_legacy:
        try:
            now = int(time.time() * 1000)
            start = now - 5 * 60 * 1000
            t0 = time.time()
            r = _get(
                f"{BASE}/fapi/v1/allForceOrders",
                {"symbol": b_symbol, "startTime": start, "limit": 1000},
                timeout=6.0
            )
            ms = (time.time() - t0) * 1000
            if r.status_code == 200:
                data = r.json()
                tot = 0.0
                for it in data:
                    try:
                        qty = float(it.get("origQty", 0.0) or 0.0)
                        px  = float(it.get("price",   0.0) or 0.0)
                        tot += qty * px
                    except Exception:
                        continue
                norm  = _norm_for_symbol(b_symbol)
                score = min(1.0, tot / max(1.0, norm))
                _log_info(f"[Liq] {b_symbol} {len(data)} orders, notionnel≈{tot:.2f} ({ms:.1f} ms)")
                return {
                    "liq_new_score": float(score),
                    "liq_score":     float(score),
                    "liq_notional_5m": float(tot),
                    "liq_imbalance_5m": 0.0,
                    "liq_source": "primary",
                }
            else:
                if r.status_code == 400:
                    _log_info(f"[Liq] {b_symbol} HTTP {r.status_code} resp={r.text}")
                else:
                    _log_warn(f"[Liq] {b_symbol} HTTP {r.status_code} resp={r.text}")
        except Exception as e:
            _log_exc(f"[Liq] {b_symbol}", e)

    # 2) Fallback PROXY via takerLongShortRatio + markPrice
    try:
        t0 = time.time()
        rr = _get(
            f"{BASE}/futures/data/takerlongshortRatio",
            {"symbol": b_symbol, "period": "5m", "limit": 1},
            timeout=6.0
        )
        ms = (time.time() - t0) * 1000
        if rr.status_code == 200:
            arr = rr.json() or []
            if arr:
                rec = arr[-1]
                buy_vol  = float(rec.get("buyVol",  0.0) or 0.0)
                sell_vol = float(rec.get("sellVol", 0.0) or 0.0)
                mark     = _mark_price(b_symbol)
                score, notionnel, imb = _score_from_proxy(buy_vol, sell_vol, mark, b_symbol)
                _log_info(
                    f"[Liq-PROXY] {b_symbol} buyVol={buy_vol} sellVol={sell_vol} mark={mark} "
                    f"-> notionnel≈{notionnel:.2f} ({ms:.1f} ms)"
                )
                return {
                    "liq_new_score": float(score),
                    "liq_score":     float(score),   # compat
                    "liq_notional_5m": float(notionnel),
                    "liq_imbalance_5m": float(imb),
                    "liq_source": "proxy",
                }
        _log_warn(f"[Liq-PROXY] {b_symbol} HTTP {rr.status_code} resp={rr.text}")
    except Exception as e:
        _log_exc(f"[Liq-PROXY] {b_symbol}", e)

    # 3) Échec -> pack neutre
    return {
        "liq_new_score": 0.0,
        "liq_score": 0.0,
        "liq_notional_5m": 0.0,
        "liq_imbalance_5m": 0.0,
        "liq_source": "none",
    }

# --------------------------------------------------------------------------------------
# OI & FUNDING -> SCORES (AJOUT)
# --------------------------------------------------------------------------------------
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if x < lo: return lo
    if x > hi: return hi
    return x

def _float_any(d: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return float(default)

def get_open_interest_hist(symbol: str, period: str = "5m", limit: int = 2) -> list:
    """
    Historique d'Open Interest (USDT-M).
    Binance: GET /futures/data/openInterestHist
      -> clés: sumOpenInterest, sumOpenInterestValue, timestamp
    """
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = _get(
            f"{BASE}/futures/data/openInterestHist",
            {"symbol": b_symbol, "period": period, "limit": max(2, min(500, limit))},
            timeout=6.0,
        )
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            arr = r.json() or []
            _log_info(f"[OI-HIST] {b_symbol} period={period} len={len(arr)} ({ms:.1f} ms)")
            return arr
        _log_warn(f"[OI-HIST] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc(f"[OI-HIST] {b_symbol}", e)
    return []

def get_oi_score(symbol: str, ref: Optional[float] = None) -> Optional[float]:
    """
    Score OI basé sur la variation % du dernier intervalle 5m.
    oi_score = clamp( |ΔOI| / OI_PREV / oi_delta_ref , 0..1 )
    - Utilise 'sumOpenInterest' si dispo, sinon 'openInterest'.
    - Retourne None si impossible de calculer (manque de données).
    """
    if ref is None:
        try:
            ref = float(getattr(SETTINGS, "oi_delta_ref", 0.02))  # 2% -> score 1.0
        except Exception:
            ref = 0.02
    hist = get_open_interest_hist(symbol, period="5m", limit=2)
    if len(hist) < 2:
        return None
    prev = hist[-2]
    last = hist[-1]
    oi_prev = _float_any(prev, "sumOpenInterest", "openInterest", default=0.0)
    oi_last = _float_any(last, "sumOpenInterest", "openInterest", default=0.0)
    if oi_prev <= 0.0:
        return None
    delta_pct = abs(oi_last - oi_prev) / oi_prev
    score = _clamp(delta_pct / (ref if ref and ref > 0 else 1e-6))
    return float(score)

def get_funding_score(symbol: str, ref: Optional[float] = None) -> Optional[float]:
    """
    Score Funding basé sur lastFundingRate (premiumIndex).
    funding_score = clamp( |funding| / funding_ref , 0..1 )
    - Retourne None si erreur API.
    """
    if ref is None:
        try:
            ref = float(getattr(SETTINGS, "funding_ref", 0.00025))  # 0.025% -> score 1.0
        except Exception:
            ref = 0.00025
    b_symbol = map_symbol_to_binance(symbol)
    try:
        t0 = time.time()
        r = _get(f"{BASE}/fapi/v1/premiumIndex", {"symbol": b_symbol}, timeout=6.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json() or {}
            fr = float(data.get("lastFundingRate", 0.0) or 0.0)
            score = _clamp(abs(fr) / (ref if ref and ref > 0 else 1e-6))
            _log_info(f"[FundingScore] {b_symbol} fr={fr} -> score={score:.3f} ({ms:.1f} ms)")
            return float(score)
        _log_warn(f"[FundingScore] {b_symbol} HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc(f"[FundingScore] {b_symbol}", e)
    return None

# --------------------------------------------------------------------------------------
# MACRO (CoinGecko gratuit)
# --------------------------------------------------------------------------------------
def get_macro_total_mcap() -> float:
    try:
        t0 = time.time()
        r = _get(f"{CG}/global", timeout=8.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[Macro] TOTAL MCAP {data} ({ms:.1f} ms)")
            return float(data.get("data", {}).get("total_market_cap", {}).get("usd", 0.0) or 0.0)
        _log_warn(f"[Macro] TOTAL MCAP HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc("[Macro] TOTAL MCAP", e)
    return 0.0

def get_macro_btc_dominance() -> float:
    try:
        t0 = time.time()
        r = _get(f"{CG}/global", timeout=8.0)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            _log_info(f"[Macro] BTC DOM {data} ({ms:.1f} ms)")
            dom_pct = float(data.get("data", {}).get("market_cap_percentage", {}).get("btc", 0.0) or 0.0)
            return dom_pct / 100.0
        _log_warn(f"[Macro] BTC DOM HTTP {r.status_code} resp={r.text}")
    except Exception as e:
        _log_exc("[Macro] BTC DOM", e)
    return 0.0

def get_macro_total2() -> float:
    tot = get_macro_total_mcap()
    dom = get_macro_btc_dominance()
    return max(0.0, tot * (1.0 - dom))
