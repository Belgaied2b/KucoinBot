import asyncio
import json
import websockets
import time
from collections import deque, defaultdict
from datetime import datetime
import requests
import numpy as np

# ✅ Configuration
BINANCE_WS = "wss://fstream.binance.com/stream"
BINANCE_REST = "https://fapi.binance.com"
SYMBOLS = ["btcusdt", "ethusdt"]  # Tu peux étendre ici
OI_CACHE = {}
COOLDOWNS = defaultdict(lambda: 0)

# 💾 Données temps réel
live_data = {
    symbol: {
        "cvd": 0,
        "oi_ok": False,
        "funding_ok": False,
        "liq_spike": False,
        "cvd_ok": False,
        "last_score": 0,
        "last_signal_time": 0
    } for symbol in SYMBOLS
}

# 📊 CVD
cvd_buffers = {symbol: deque(maxlen=1000) for symbol in SYMBOLS}

# 🔄 Récupère l'OI par REST
def fetch_open_interest(symbol):
    try:
        url = f"{BINANCE_REST}/futures/data/openInterestHist"
        params = {"symbol": symbol.upper(), "period": "5m", "limit": 10}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return False
        prev = float(data[-2]["sumOpenInterest"])
        curr = float(data[-1]["sumOpenInterest"])
        return curr > prev * 1.01
    except:
        return False

# 🔄 Funding
def fetch_funding(symbol):
    try:
        url = f"{BINANCE_REST}/fapi/v1/fundingRate"
        params = {"symbol": symbol.upper(), "limit": 2}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data[-1]["fundingRate"]) < 0.0001
    except:
        return False

# 🔄 Liquidations
def fetch_liquidations(symbol):
    try:
        url = f"{BINANCE_REST}/fapi/v1/allForceOrders"
        r = requests.get(url, params={"symbol": symbol.upper(), "limit": 50}, timeout=10)
        r.raise_for_status()
        data = r.json()
        total = 0
        for d in data:
            price = float(d.get("price", 0))
            qty = float(d.get("origQty", 0))
            total += price * qty
        return total > 50000
    except:
        return False

# 📡 Websocket
async def start_ws():
    streams = "/".join([f"{s}@trade" for s in SYMBOLS])
    async with websockets.connect(f"{BINANCE_WS}?streams={streams}") as ws:
        async for msg in ws:
            data = json.loads(msg)
            symbol = data["stream"].split("@")[0]
            price = float(data["data"]["p"])
            qty = float(data["data"]["q"])
            is_buyer_maker = data["data"]["m"]
            signed_qty = -qty if is_buyer_maker else qty
            cvd_buffers[symbol].append(signed_qty)
            live_data[symbol]["cvd"] = sum(cvd_buffers[symbol])

# 🧠 Analyse institutionnelle live
async def institutional_loop():
    while True:
        for symbol in SYMBOLS:
            oi_ok = fetch_open_interest(symbol)
            funding_ok = fetch_funding(symbol)
            liq_spike = fetch_liquidations(symbol)
            cvd_now = live_data[symbol]["cvd"]
            cvd_ok = cvd_now > 0

            score = sum([
                oi_ok,
                funding_ok,
                liq_spike,
                cvd_ok
            ])

            live_data[symbol].update({
                "oi_ok": oi_ok,
                "funding_ok": funding_ok,
                "liq_spike": liq_spike,
                "cvd_ok": cvd_ok,
                "last_score": score
            })

            # ⏱️ Cooldown anti-spam (10 min)
            now = time.time()
            if score >= 3 and now - live_data[symbol]["last_signal_time"] > 600:
                print(f"📈 Signal institutionnel détecté sur {symbol.upper()} (score={score}/4)")
                live_data[symbol]["last_signal_time"] = now

        await asyncio.sleep(60)

# 🚀 Lance le bot
async def launch():
    await asyncio.gather(
        start_ws(),
        institutional_loop()
    )

if __name__ == "__main__":
    asyncio.run(launch())
