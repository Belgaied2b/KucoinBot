# =====================================================================
# duplicate_guard.py — Empêche envoi du même signal plusieurs fois
# =====================================================================
import time


class DuplicateGuard:
    """
    Stocke les empreintes des signaux récents afin
    d'éviter les doublons (même symbole, même side, même zone).
    """

    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self.cache = {}

    def seen(self, fingerprint: str) -> bool:
        now = time.time()

        # Cleanup vieux items
        keys_to_delete = [k for k, ts in self.cache.items() if now - ts > self.ttl]
        for k in keys_to_delete:
            del self.cache[k]

        # Déjà vu ?
        if fingerprint in self.cache:
            return True

        # Sinon on l’ajoute
        self.cache[fingerprint] = now
        return False
