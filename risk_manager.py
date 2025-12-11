# =====================================================================
# risk_manager.py — Desk Supreme Risk Engine (2025)
# =====================================================================
# Rôle :
#   - Centraliser toutes les règles de risque du bot :
#       * risque fixe par trade (en USDT)
#       * max pertes / jour (hard stop)
#       * max trades / jour
#       * max positions ouvertes
#       * limite directionnelle (trop de LONG / SHORT)
#       * anti-tilt : cooldown après série de pertes
#   - Fournir une API simple au scanner :
#       * can_open(symbol, side) -> (bool, reason)
#       * register_open(symbol, side, notional, risk)
#       * register_closed(symbol, side, pnl)
#       * risk_for_this_trade() -> float
#
#   NB : Ce module garde son état en mémoire. Si tu veux le rendre
#        persistant (fichier JSON), on pourra l’étendre ensuite.
# =====================================================================

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Any, Tuple, Optional, List


# =====================================================================
# CONFIG — À AJUSTER DANS UN SECOND TEMPS SI BESOIN
# =====================================================================

@dataclass
class RiskConfig:
    # Risque théorique par trade (en USDT) — cohérent avec ta marge ~20 USDT
    risk_per_trade: float = 20.0

    # Pertes max / jour (hard stop) — ex : 3 trades perdants consécutifs
    max_daily_loss: float = 60.0

    # Nombre max de trades par jour
    max_trades_per_day: int = 20

    # Nombre max de positions ouvertes en même temps
    max_open_positions: int = 5

    # Limite directionnelle : max positions LONG et SHORT
    max_long_positions: int = 4
    max_short_positions: int = 4

    # Anti-tilt : si on dépasse cette suite de pertes, on impose un cooldown
    max_consecutive_losses: int = 3

    # Durée du cooldown en secondes (ex : 1h)
    tilt_cooldown_seconds: int = 60 * 60

    # Multiplicateur de risque si on est en drawdown (optionnel)
    # ex : si daily_loss < -risk_per_trade * 2, on réduit le risque
    drawdown_risk_factor: float = 0.5


# =====================================================================
# ÉTAT INTERNE
# =====================================================================

@dataclass
class DailyState:
    date_key: str
    trades_opened: int = 0
    pnl: float = 0.0
    losses_count: int = 0


@dataclass
class PositionState:
    symbol: str
    side: str  # "LONG"/"SHORT"
    notional: float
    risk: float
    opened_at: float = field(default_factory=lambda: time.time())


class RiskManager:
    """
    Risk manager institutionnel suprême.

    Utilisation typique dans scanner.py :

        rm = RiskManager()

        allowed, reason = rm.can_open(symbol, "LONG")
        if not allowed:
            log / telegram "[RISK] veto ..."
            return

        risk_usdt = rm.risk_for_this_trade()
        # sizing basé sur risk_usdt, si tu veux

        rm.register_open(symbol, "LONG", notional=..., risk=risk_usdt)

        # plus tard, quand la position est close :
        rm.register_closed(symbol, "LONG", pnl=+15.0)  # ou -20.0 etc.
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config: RiskConfig = config or RiskConfig()

        # État "jour"
        self._daily: Optional[DailyState] = None

        # Positions ouvertes : symbol -> PositionState
        self.open_positions: Dict[str, PositionState] = {}

        # Compteur directionnel
        self.direction_counts = {"LONG": 0, "SHORT": 0}

        # Tilt / cooldown
        self._tilt_active: bool = False
        self._tilt_activated_at: float = 0.0

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _current_date_key(self) -> str:
        """
        Renvoie une clé simple pour la journée courante (YYYY-MM-DD).
        """
        return time.strftime("%Y-%m-%d", time.localtime())

    def _ensure_daily_state(self):
        """
        Initialise / reset l'état quotidien si changement de jour.
        """
        today = self._current_date_key()
        if self._daily is None or self._daily.date_key != today:
            self._daily = DailyState(date_key=today)
            # On reset aussi le tilt journalier
            self._tilt_active = False
            self._tilt_activated_at = 0.0

    def _daily_loss(self) -> float:
        self._ensure_daily_state()
        return float(self._daily.pnl if self._daily else 0.0)

    def _daily_trades(self) -> int:
        self._ensure_daily_state()
        return int(self._daily.trades_opened if self._daily else 0)

    def _daily_losses(self) -> int:
        self._ensure_daily_state()
        return int(self._daily.losses_count if self._daily else 0)

    def _is_tilt_active(self) -> bool:
        if not self._tilt_active:
            return False
        elapsed = time.time() - self._tilt_activated_at
        if elapsed >= self.config.tilt_cooldown_seconds:
            # cooldown fini
            self._tilt_active = False
            self._tilt_activated_at = 0.0
            return False
        return True

    # ------------------------------------------------------------------
    # API principale
    # ------------------------------------------------------------------

    def can_open(self, symbol: str, side: str) -> Tuple[bool, str]:
        """
        Vérifie si on est autorisé à ouvrir une nouvelle position.

        Args:
            symbol: "BTCUSDT", "AVAXUSDT", etc.
            side: "BUY"/"SELL" ou "LONG"/"SHORT"

        Returns:
            (allowed: bool, reason: str)
        """
        self._ensure_daily_state()
        side = side.upper()
        if side == "BUY":
            side = "LONG"
        elif side == "SELL":
            side = "SHORT"

        # 1) Cooldown tilt ?
        if self._is_tilt_active():
            return False, "tilt_cooldown"

        # 2) Limite trades/jour
        if self._daily_trades() >= self.config.max_trades_per_day:
            return False, "max_trades_per_day_reached"

        # 3) Limite de perte quotidienne
        if self._daily_loss() <= -abs(self.config.max_daily_loss):
            return False, "max_daily_loss_reached"

        # 4) Limite de positions ouvertes global
        if len(self.open_positions) >= self.config.max_open_positions:
            return False, "max_open_positions_reached"

        # 5) Limite directionnelle
        if side == "LONG" and self.direction_counts["LONG"] >= self.config.max_long_positions:
            return False, "max_long_exposure"
        if side == "SHORT" and self.direction_counts["SHORT"] >= self.config.max_short_positions:
            return False, "max_short_exposure"

        # 6) Déjà une position ouverte sur ce symbole dans le même sens ?
        if symbol in self.open_positions:
            pos = self.open_positions[symbol]
            if pos.side == side:
                return False, "position_already_open_same_side"

        # OK
        return True, "OK"

    # ------------------------------------------------------------------

    def risk_for_this_trade(self) -> float:
        """
        Montant de risque (en USDT) autorisé pour le prochain trade.

        Peut être ajusté dynamiquement selon le drawdown :
          - Si on est en pertes journalières, on réduit le risque.
        """
        self._ensure_daily_state()
        base_risk = float(self.config.risk_per_trade)
        dloss = self._daily_loss()

        if dloss < -2.0 * base_risk:
            # en drawdown, on réduit le risque
            return float(base_risk * self.config.drawdown_risk_factor)
        return base_risk

    # ------------------------------------------------------------------

    def register_open(self, symbol: str, side: str, notional: float, risk: float):
        """
        À appeler quand on ouvre effectivement une position.

        - symbol : ex. "AVAXUSDT"
        - side   : "LONG"/"SHORT"/"BUY"/"SELL"
        - notional : notionnel approx (en USDT)
        - risk     : risque utilisé (en USDT)
        """
        self._ensure_daily_state()

        side = side.upper()
        if side == "BUY":
            side = "LONG"
        elif side == "SELL":
            side = "SHORT"

        self.open_positions[symbol] = PositionState(
            symbol=symbol,
            side=side,
            notional=float(notional),
            risk=float(risk),
        )

        self.direction_counts[side] = self.direction_counts.get(side, 0) + 1
        self._daily.trades_opened += 1

    # ------------------------------------------------------------------

    def register_closed(self, symbol: str, side: str, pnl: float):
        """
        À appeler quand la position est entièrement close.

        - pnl : profit ou perte en USDT (approx)
        """
        self._ensure_daily_state()

        side = side.upper()
        if side == "BUY":
            side = "LONG"
        elif side == "SELL":
            side = "SHORT"

        # Update PnL journalier
        self._daily.pnl += float(pnl)

        # Update pertes consécutives
        if pnl < 0:
            self._daily.losses_count += 1
        else:
            self._daily.losses_count = 0

        # Tilt ?
        if self._daily.losses_count >= self.config.max_consecutive_losses:
            self._tilt_active = True
            self._tilt_activated_at = time.time()

        # Fermer la position dans l'état
        pos = self.open_positions.pop(symbol, None)
        if pos is not None:
            self.direction_counts[pos.side] = max(0, self.direction_counts.get(pos.side, 0) - 1)
        else:
            # si on ne la trouve pas, on décrémente sur le side annoncé
            self.direction_counts[side] = max(0, self.direction_counts.get(side, 0) - 1)

    # ------------------------------------------------------------------

    def snapshot_state(self) -> Dict[str, Any]:
        """
        Petit snapshot pour debug / logs / monitoring externe.
        """
        self._ensure_daily_state()

        return {
            "date": self._daily.date_key if self._daily else None,
            "daily_pnl": self._daily.pnl if self._daily else 0.0,
            "daily_trades": self._daily.trades_opened if self._daily else 0,
            "daily_losses": self._daily.losses_count if self._daily else 0,
            "tilt_active": self._is_tilt_active(),
            "open_positions": {
                sym: {
                    "side": pos.side,
                    "notional": pos.notional,
                    "risk": pos.risk,
                    "opened_at": pos.opened_at,
                }
                for sym, pos in self.open_positions.items()
            },
            "direction_counts": dict(self.direction_counts),
        }
