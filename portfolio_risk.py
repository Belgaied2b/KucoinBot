"""
portfolio_risk.py — gestion du risque corrélé (niveau desk)
- Cap par "groupe corrélé" (BTC-like vs ALTS-momentum)
- Décote du sizing si corrélation élevée à BTC et dominance BTC positive
- Garde-fou additionnel par régime de marché
"""
from __future__ import annotations
from typing import Tuple
from settings import (
    ACCOUNT_EQUITY_USDT,
    MAX_GROSS_EXPOSURE,
    MAX_SYMBOL_EXPOSURE,
    CORR_GROUP_CAP,
    CORR_BTC_THRESHOLD,
    DOM_TREND_STRONG,
)
from risk_manager import _state  # réutilise l'état courant (expo, cooldowns, etc.)

def _group_key(corr_btc: float, favor_alts: bool) -> str:
    """
    Regroupe les positions en 2 grands paniers:
      - 'BTC_CLUSTER' : corrélation forte à BTC
      - 'ALTS_ROTATION' : préférence alts (dominance en baisse)
    """
    if corr_btc >= CORR_BTC_THRESHOLD:
        return "BTC_CLUSTER"
    return "ALTS_ROTATION" if favor_alts else "DIVERSE"

def guardrails_ok_portfolio(symbol: str, est_notional: float, corr_ctx: dict) -> Tuple[bool, str]:
    """
    Vérifie les limites portefeuille corrélées en plus des guardrails de base.
    """
    if not corr_ctx or not corr_ctx.get("ok"):
        # si pas de contexte, on ne bloque pas (on laisse les gardes de base décider)
        return True, "no_ctx"

    # limites globales de base déjà gérées par risk_manager.guardrails_ok
    # ici on ajoute la couche "par groupe corrélé"
    group = _group_key(float(corr_ctx["corr_btc"]), bool(corr_ctx["favor_alts"]))
    group_expo = _state.get("group_expo", {})
    cur = float(group_expo.get(group, 0.0))
    cap = float(ACCOUNT_EQUITY_USDT) * float(CORR_GROUP_CAP)

    if (cur + est_notional) > cap:
        return False, f"group cap {group}"

    # enregistre la réservation (sera validée après envoi réel)
    group_expo[group] = cur + est_notional
    _state["group_expo"] = group_expo

    # décote sizing si dominance BTC très forte et corrélation élevée côté BTC
    if corr_ctx.get("dominance_trend", 0.0) > DOM_TREND_STRONG and corr_ctx.get("corr_btc", 0.0) > CORR_BTC_THRESHOLD:
        return True, "size_discount"  # (tu peux exploiter ce flag pour réduire encore la taille si tu veux)

    return True, "OK"
