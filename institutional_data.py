import asyncio
import websockets
import json
import time
from collections import deque

# Stockage local du CVD et volume par symbole
live_data = {}

# Configuration des symboles à suivre (USDT uniquement pour futures)
SYMBOLS = ["btcusdt", "ethusdt", "solusdt", "linkusdt", "avaxusdt"]

# Taille de la fenêtre pour le calcul du CVD
WINDOW = 100

def init_symbol(symbol):
    if symbol not in live_data:
        live_data[symbol] = {
            "cvd": deque(maxlen=WINDOW),
            "volume": deque(maxlen=WINDOW),
            "delta": deque(maxlen=WINDOW),
            "last_cvd": 0
        }

async def process_message(msg):
    if "s" not in msg or "p" not in msg or "q" not in msg:
        return
    symbol = msg["s"].lower()
    price = float(msg["p"])
    quantity = float(msg["q"])
    side = msg["m"]  # True = sell, False = buy

    init_symbol(symbol)

    delta = -quantity if side else quantity
    last_cvd = live_data[symbol]["last_cvd"] + delta
    live_data[symbol]["last_cvd"] = last_cvd

    live_data[symbol]["cvd"].append(last_cvd)
    live_data[symbol]["volume"].append(quantity)
    live_data[symbol]["delta"].append(delta)

    # Debug CVD
    if len(live_data[symbol]["cvd"]) == WINDOW:
        recent_cvd = list(live_data[symbol]["cvd"])
        if recent_cvd[-1] > recent_cvd[0]:
            trend = "CVD UP"
        elif recent_cvd[-1] < recent_cvd[0]:
            trend = "CVD DOWN"
        else:
            trend = "CVD FLAT"
        print(f"[{symbol.upper()}] {trend} | Δ: {round(recent_cvd[-1] - recent_cvd[0], 2)}")

async def subscribe_trades(ws):
    params = [f"{symbol}@trade" for symbol in SYMBOLS]
    payload = {
        "method": "SUBSCRIBE",
        "params": params,
        "id": 1
    }
    await ws.send(json.dumps(payload))

async def run_websocket():
    uri = "wss://fstream.binance.com/ws"
    async with websockets.connect(uri) as ws:
        await subscribe_trades(ws)
        print("✅ WebSocket connecté aux symboles :", SYMBOLS)

        while True:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(message)
                await process_message(data)
            except asyncio.TimeoutError:
                print("⏱️ Timeout WebSocket, tentative de reconnexion...")
                break
            except Exception as e:
                print("❌ Erreur WebSocket :", e)
                break

# Lancement auto en tâche de fond
def start_live_stream():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        loop.run_until_complete(run_websocket())
        time.sleep(5)
