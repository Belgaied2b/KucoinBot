# alert_manager.py
import time

class AlertManager:
    """
    Empêche les doublons d'alerte sur une même zone/signal
    pendant un cooldown (en secondes).
    """
    def __init__(self, cooldown: int = 300):
        self.cooldown = cooldown
        self.last = {}  # key -> timestamp

    def can_send(self, key: tuple) -> bool:
        now = time.time()
        t0 = self.last.get(key, 0)
        if now - t0 < self.cooldown:
            return False
        self.last[key] = now
        return True
