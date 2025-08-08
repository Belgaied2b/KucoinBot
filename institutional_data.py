import asyncio
import websockets
import json
import time
from collections import deque
import threading

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
            "last_cvd": 0,
            "last_score": 0
        }

def compute_score(symbol):
    data = live_data[symbol]
    if len(data["cvd"]) < WINDOW:
        return 0

    recent_cvd = list(data["cvd"])
    recent_delta = list(data["delta"])

    delta_cvd = recent_cvd[-1] - recent_cvd[0]
    delta_volume = sum(data["volume"]) / WINDOW

    # Scoring simple basé sur la tendance CVD et delta
    score = 0
    if delta_cvd > 0:
        score += 1
    if delta_cvd > delta_volume:
        score += 1
    if sum(recent_delta[-5:]) > 0:
        score += 1
    if recent_delta[-1] > 0:
        score += 1

    data["last_score"] = score

    print(f"[{symbol.upper()}] 🔍 Score live = {score}/4 | ΔCVD: {round(delta_cvd,2)} | ΔVol moy: {round(delta_volume,2)}")

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

    if len(live_data[symbol]["cvd"]) == WINDOW:
        compute_score(symbol)

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

# Lancement automatique du WebSocket en tâche de fond
def start_live_stream():
    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            loop.run_until_complete(run_websocket())
            time.sleep(5)
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()

# Démarrer automatiquement le stream à l'import
start_live_stream()
