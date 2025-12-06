# =====================================================================
# analyze_signal.py — Cerveau du bot institutionnel Bitget
# Version corrigée, optimisée, compatible scanner / structure / momentum
# =====================================================================

import pandas as pd
from typing import Dict, Any, Optional

from structure_utils import analyze_structure, htf_confirm
from indicators import (
    institutional_momentum,
    compute_ote,
    true_atr,
)
from stops import compute_stop_loss
from tp_utils import compute_tp1
from institutional_data import InstitutionalData


class SignalAnalyzer:
    """
    Analyse complète d’un signal institutionnel strict.
    Conditions :
        - Structure alignée (trend, bos/cos/choch)
        - HTF confirmation (H4)
        - Score institutionnel >= 2
        - Momentum institutionnel propre
        - Entrée dans la zone OTE
        - RR >= 1.6 (dynamique)
    """

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.inst = InstitutionalData(api_key, api_secret, api_passphrase)

    # =================================================================
    async def analyze(
        self,
        symbol: str,
        df_h1: pd.DataFrame,
        df_h4: pd.DataFrame,
        contract: Dict[str, Any],
        rr_min: float = 1.6,
    ) -> Optional[Dict[str, Any]]:
        """
        Retourne un signal structuré OU None si le trade est rejeté.
        """

        # ------------------------------------------------------------
        # 1) STRUCTURE H1
        # ------------------------------------------------------------
        struct = analyze_structure(df_h1)
        trend = struct["trend"]

        if trend not in ("LONG", "SHORT"):
            return None

        # BOS, COS ou CHOCH doivent exister
        if not struct["bos"] and not struct["cos"] and not struct["choch"]:
            return None

        # ------------------------------------------------------------
        # 2) CONFIRMATION H4
        # ------------------------------------------------------------
        htf = htf_confirm(df_h4)
        if htf != trend:
            return None

        # ------------------------------------------------------------
        # 3) MOMENTUM INSTITUTIONNEL
        # ------------------------------------------------------------
        mom = institutional_momentum(df_h1)

        if trend == "LONG" and mom not in ("BULLISH", "STRONG_BULLISH"):
            return None

        if trend == "SHORT" and mom not in ("BEARISH", "STRONG_BEARISH"):
            return None

        # ------------------------------------------------------------
        # 4) SCORE INSTITUTIONNEL (Funding, OI, CVD, Liquidations)
        # ------------------------------------------------------------
        inst = await self.inst.compute_score(symbol)

        if inst["score"] < 2.0:
            return None

        # ------------------------------------------------------------
        # 5) STOP LOSS institutionnel
        # ------------------------------------------------------------
        price_place = int(contract.get("pricePlace", 2))
        tick = 1 / (10 ** price_place)

        sl = compute_stop_loss(df_h1, trend, tick)

        # ------------------------------------------------------------
        # 6) RR THEORIQUE MINIMAL
        # ------------------------------------------------------------
        entry = float(df_h1["close"].iloc[-1])
        risk = abs(entry - sl)

        tp_test = entry + rr_min * risk if trend == "LONG" else entry - rr_min * risk

        # Rejet si mathématiquement impossible
        if trend == "LONG" and tp_test <= entry:
            return None

        if trend == "SHORT" and tp_test >= entry:
            return None

        # ------------------------------------------------------------
        # 7) VALIDATION OTE
        # ------------------------------------------------------------
        ote = compute_ote(df_h1, trend)
        ote62 = ote.get("ote_62")
        ote705 = ote.get("ote_705")

        in_ote = False

        if trend == "LONG":
            if ote62 and ote705 and ote62 <= entry <= ote705:
                in_ote = True
        else:
            if ote62 and ote705 and ote705 <= entry <= ote62:
                in_ote = True

        if not in_ote:
            return None

        # ------------------------------------------------------------
        # 8) TP1 dynamique institutionnel
        # ------------------------------------------------------------
        tp1, rr_used = compute_tp1(
            entry=entry,
            sl=sl,
            bias=trend,
            df=df_h1,
            tick=tick,
        )

        if rr_used < rr_min:
            return None

        # ------------------------------------------------------------
        # 9) PACK RESULT FINAL
        # ------------------------------------------------------------
        return {
            "side": trend,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "rr": rr_used,
            "institutional": inst,
            "structure": struct,
            "ote": {"62": ote62, "705": ote705},
        }
