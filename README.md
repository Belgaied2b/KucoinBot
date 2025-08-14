# Institution++++ v1.1-bis (KuCoin Futures, Binance feed)

## Caractéristiques
- Flux temps réel: Trades (CVD/Δ), Open Interest, Funding, Liquidations, Carnet (depth5@100ms) + best bid/ask/mid/spread.
- Scoring institutionnel pondéré + setups pro (Initiative Breakout, VWAP Reversion, Stop-Run Reversal).
- Gestion du risque: SL au-delà des pools (equal highs/lows), TP1 partiel → BE → trailing ATR, TP2 final.
- Exécution KuCoin Futures: entrée LIMIT (post-only maker), requotes, timeout, fallback **IOC** optionnel, sorties reduce-only MARKET.
- WebSocket privé KuCoin: suivi de fills/positions + **avgFillPrice** ; persistance JSON pour reprise.
- Meta symboles KuCoin: tickSize / pricePrecision auto par symbole.

## Installation locale
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env  # si vous ne passez pas par Railway
python main.py
```

## Déploiement Railway
- Définissez toutes les variables d'environnement du `.env.sample` dans Railway.
- Command: `python main.py`

## Fichiers
Voir l'arborescence du projet pour la liste complète des fichiers.
