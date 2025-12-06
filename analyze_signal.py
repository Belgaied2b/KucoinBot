# =====================================================================
# analyze_signal.py — Cerveau du bot institutionnel Bitget
# =====================================================================
import pandas as pd
from typing import Dict, Any, Optional

from .structure_utils import analyze_structure, htf_confirm
from .indicators import (
    institutional_momentum,
    compute_ote,
    true_atr,
)
from .stops import compute_stop_loss
from .tp_utils import compute_tp1
from .institutional_data import InstitutionalData


class SignalAnalyzer:
    """
    Analyse complète d’un signal institutionnel strict.
    Conditions :
        - Structure alignée (trend, bos/cos)
        - HTF confirmation (H4)
        - Score institutionnel >= 2
        - Momentum propre
        - Entrée en zone OTE
        - RR >= 1.6
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
        Analyse complète.
        Retourne None si le signal est rejeté,
        sinon retourne un dictionnaire avec tous les détails du trade.
        """

        # ------------------------------------------------------------
        # 1) Structure H1
        # ------------------------------------------------------------
        struct = analyze_structure(df_h1)
        trend = struct["trend"]

        if trend not in ("LONG", "SHORT"):
            return None  # pas de structure claire

        bos = struct["bos"]
        cos = struct["cos"]

        if not bos and not cos:
            return None  # pas de rupture validée

        # ------------------------------------------------------------
        # 2) HTF confirmation (H4)
        # ------------------------------------------------------------
        htf = htf_confirm(df_h4)
        if htf != trend:
            return None  # pas aligné HTF

        # ------------------------------------------------------------
        # 3) Momentum
        # ------------------------------------------------------------
        mom = institutional_momentum(df_h1)
        if trend == "LONG" and mom != "BULLISH":
            return None
        if trend == "SHORT" and mom != "BEARISH":
            return None

        # ------------------------------------------------------------
        # 4) Institutional Score
        # ------------------------------------------------------------
        inst = await self.inst.compute_score(symbol)
        if inst["score"] < 2.0:
            return None

        # ------------------------------------------------------------
        # 5) Stop-loss institutionnel
        # ------------------------------------------------------------
        tick = float(contract.get("pricePlace", 2))
        tick = 1 / (10 ** tick)  # Bitget pricePlace → tickSize

        sl = compute_stop_loss(df_h1, trend, tick)

        # ------------------------------------------------------------
        # 6) RR Check (minimum 1.6)
        # ------------------------------------------------------------
        close = float(df_h1["close"].iloc[-1])
        risk = abs(close - sl)

        # TP1 target theoretical (RR = 1.6)
        tp_theoretical = close + 1.6 * risk if trend == "LONG" else close - 1.6 * risk

        if trend == "LONG" and tp_theoretical <= close:
            return None
        if trend == "SHORT" and tp_theoretical >= close:
            return None

        # ------------------------------------------------------------
        # 7) OTE validation
        # ------------------------------------------------------------
        ote = compute_ote(df_h1, trend)
        ote62 = ote.get("ote_62")
        ote705 = ote.get("ote_705")

        in_ote = False
        if trend == "LONG":
            if ote62 and ote705 and close >= ote62 and close <= ote705:
                in_ote = True
        else:
            if ote62 and ote705 and close <= ote62 and close >= ote705:
                in_ote = True

        if not in_ote:
            return None

        # ------------------------------------------------------------
        # 8) TP1 Dyn clamp
        # ------------------------------------------------------------
        tp1, rr_used = compute_tp1(
            entry=close,
            sl=sl,
            bias=trend,
            df=df_h1,
            tick=tick,
        )

        if rr_used < rr_min:
            return None

        # ------------------------------------------------------------
        # Pack result
        # ------------------------------------------------------------
        return {
            "side": trend,
            "entry": close,
            "sl": sl,
            "tp1": tp1,
            "rr": rr_used,
            "institutional": inst,
            "structure": struct,
            "ote": {"62": ote62, "705": ote705},
        }
