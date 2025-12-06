# =====================================================================
# risk_manager.py — Gestion institutionnelle du risque (Option A strict)
# =====================================================================
import time
from typing import Dict, Any


class RiskManager:
    """
    Risk manager institutionnel strict.
    Fonctionnalités :
        - Risque fixe par trade (USDT)
        - Anti-overexposure (max positions)
        - Anti-drawdown journalier
        - Cooldown après perte
        - Limite d'exposition directionnelle
        - Hard filter pour éviter surcharge
    """

    def __init__(
        self,
        max_daily_loss_usdt: float = 100,
        max_positions: int = 3,
        risk_per_trade_usdt: float = 20,
        cooldown_seconds: int = 300,
        max_directional_trades: int = 2,
    ):
        self.max_daily_loss = max_daily_loss_usdt
        self.max_positions = max_positions
        self.risk_per_trade = risk_per_trade_usdt
        self.cooldown = cooldown_seconds
        self.max_directional_trades = max_directional_trades

        self.daily_loss = 0.0
        self.last_trade_time = 0
        self.direction_counts = {"LONG": 0, "SHORT": 0}
        self.open_positions = 0

    # ------------------------------------------------------------------
    def register_trade(self, side: str):
        """À appeler après ouverture d'un trade."""
        side = side.upper()
        self.open_positions += 1
        self.direction_counts[side] += 1
        self.last_trade_time = time.time()

    # ------------------------------------------------------------------
    def register_loss(self, amount: float):
        """À appeler si un SL est touché."""
        self.daily_loss += abs(amount)

    # ------------------------------------------------------------------
    def reset_daily(self):
        """Reset journalier des pertes."""
        self.daily_loss = 0.0
        self.direction_counts = {"LONG": 0, "SHORT": 0}
        self.open_positions = 0

    # ------------------------------------------------------------------
    def can_trade(self, side: str) -> (bool, str):
        """
        Vérifie si un trade est autorisé.
        Retourne (bool, reason)
        """
        now = time.time()

        # 1) Cooldown
        if now - self.last_trade_time < self.cooldown:
            return False, "COOLDOWN_ACTIVE"

        # 2) Max positions
        if self.open_positions >= self.max_positions:
            return False, "MAX_POSITIONS_REACHED"

        # 3) Max directional trades
        if self.direction_counts[side.upper()] >= self.max_directional_trades:
            return False, "MAX_DIRECTIONAL_EXPOSURE"

        # 4) Daily loss protection
        if self.daily_loss >= self.max_daily_loss:
            return False, "MAX_DAILY_LOSS_REACHED"

        # Autorisé
        return True, "OK"

    # ------------------------------------------------------------------
    def risk_for_this_trade(self) -> float:
        """Montant exact de risque USDT pour sizing."""
        return float(self.risk_per_trade)

    # ------------------------------------------------------------------
    def register_position_closed(self, side: str):
        side = side.upper()
        self.open_positions = max(0, self.open_positions - 1)
        self.direction_counts[side] = max(0, self.direction_counts[side] - 1)
