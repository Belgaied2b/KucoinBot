# Pack de remplacement — Bot Pro 2025

Ce pack remplace/ajoute les fichiers suivants pour passer en mode exécution pro (SFI post-only pegged-to-mid, seuil insti adaptatif, risk guard, meta-policy, MFE/MAE, event-driven).

## Fichiers inclus
- `scanner.py` — version scan finale (avec SFI, seuil insti adaptatif, perf)
- `main_event.py` — boucle event-driven (recommandée)
- `execution_sfi.py` — Smart Fill Infrastructure
- `risk_guard.py` — gardes-fous (kill-switchs, cooldowns, exposition corrélée)
- `meta_policy.py` — détection de régime + bandit bayésien
- `perf_metrics.py` — MFE/MAE + export CSV
- `ws_router.py` — EventBus + PollingSource
- `analyze_bridge.py` — pont d’analyse vers ton `analyze_signal.py`
- `kucoin_adapter.py` — pont exécution vers ta classe `KucoinTrader`

## Ce que ça change
- Entrées **post-only** en **2 tranches** pegged-to-mid, re-quote si file défavorable.
- **Seuil insti adaptatif** (quantile 70 % sur fenêtre glissante) avec plancher.
- **Risk guard** : latence/stale, drawdown/jour, pertes consécutives, exposition cluster.
- **Mesures** : MFE/MAE loggés automatiquement.
- **Mode event-driven** prêt (sinon conserve `scanner.py` en mode scan).

## Variables d’environnement recommandées
```
ORDER_VALUE_USDT=20
REQ_SCORE_FLOOR=2.0
INST_Q=0.70
INST_WINDOW=200
POST_ONLY_DEFAULT=1
PEGMID_SPLIT=0.6,0.4
TICK_DEFAULT=0.01
QUEUE_THRESHOLD=2000
REQUOTE_COOLDOWN_MS=800
DAILY_DD_LIMIT_PCT=3.0
MAX_CONSEC_LOSSES=3
COOLDOWN_MIN=30
MAX_LOSS_PER_SYMBOL_USDT=50
CLUSTER_MAP=BTC:mega;ETH:mega;PEPE:meme;DOGE:meme
CLUSTER_MAX_EXPOSURE=3
```

## Intégration
1. Dépose *tous* ces fichiers à la racine de ton projet.
2. Lance en **scan** : `python scanner.py`
3. Ou en **event-driven** : `python main_event.py`

> Pas besoin de modifier ton `kucoin_trader.py` ni `analyze_signal.py` : les **adapters** s’occupent de la compatibilité.
