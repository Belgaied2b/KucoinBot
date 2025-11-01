"""
correlations.py — Contexte inter-marchés institutionnel
- Corrélation rolling avec BTC (60 périodes)
- Proxy dominance: écart momentum BTC vs panier d'alts (moyenne des retours top-N)
- Score de rotation (alts vs BTC)
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, Tuple

from kucoin_utils import fetch_klines, fetch_all_symbols

_ROLL = 60  # 60 * 1h = 2.5 jours environ
_ALT_UNIVERSE = 20  # nombre d'alts pour TOTAL2 proxy

def _pct(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0)

def _rolling_corr(a: pd.Series, b: pd.Series, win: int) -> float:
    try:
        return float(a.tail(win).corr(b.tail(win)))
    except Exception:
        return 0.0

def _alts_basket_returns(exclude: str) -> pd.Series:
    syms = fetch_all_symbols(limit=60)
    alts = [s for s in syms if s != "XBTUSDTM"][:_ALT_UNIVERSE]
    if not alts:
        return pd.Series([0.0])
    rets = []
    for s in alts:
        df = fetch_klines(s, "1h", 300)
        if df.empty:
            continue
        rets.append(_pct(df["close"]))
    if not rets:
        return pd.Series([0.0])
    # moyenne des retours (proxy TOTAL2)
    L = min(len(r) for r in rets)
    stack = np.vstack([r.tail(L).to_numpy() for r in rets])
    mean_ret = stack.mean(axis=0)
    idx = rets[0].tail(L).index
    return pd.Series(mean_ret, index=idx)

def market_context(symbol: str, df_symbol: pd.DataFrame) -> Dict:
    """
    Retourne un contexte inter-marchés:
      - corr_btc: corrélation rolling(60) du symbole avec BTC
      - dom_trend: BTC vs ALTS momentum (pos = BTC surperforme, nég = rotation vers alts)
      - favor_alts / favor_btc
      - regime: 'risk_on' / 'risk_off' approx
    """
    # BTC futures KuCoin
    btc = fetch_klines("XBTUSDTM", "1h", 300)
    if btc.empty or df_symbol.empty:
        return {"ok": False}

    r_sym = _pct(df_symbol["close"])
    r_btc = _pct(btc["close"])

    corr_btc = _rolling_corr(r_sym, r_btc, _ROLL)

    # panier ALTS (TOTAL2 proxy)
    r_alts = _alts_basket_returns(exclude="XBTUSDTM")
    # aligne
    L = min(len(r_btc), len(r_alts))
    if L < 20:
        dom_trend = 0.0
    else:
        mbtc = float(pd.Series(r_btc).tail(L).rolling(24).mean().iloc[-1])
        malts = float(pd.Series(r_alts).tail(L).rolling(24).mean().iloc[-1])
        dom_trend = mbtc - malts  # >0 BTC domine, <0 rotation vers alts

    favor_alts = dom_trend < 0
    favor_btc = dom_trend > 0
    # régime simple: variance BTC vs ALTS
    var_btc = float(pd.Series(r_btc).tail(48).var())
    var_alts = float(pd.Series(r_alts).tail(48).var()) if len(r_alts) >= 48 else var_btc
    regime = "risk_on" if favor_alts and var_alts <= 2.5 * var_btc else "mixed" if favor_alts else "risk_off"

    return {
        "ok": True,
        "corr_btc": float(corr_btc),
        "dominance_trend": float(dom_trend),
        "favor_alts": bool(favor_alts),
        "favor_btc": bool(favor_btc),
        "regime": regime,
    }
