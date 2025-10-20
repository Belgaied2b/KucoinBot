import asyncio, json, logging, websockets, os
from typing import Dict, Any, Callable
from .symbol_map import to_binance, to_okx, to_bybit

log = logging.getLogger("ws.venues")

class VenueFeed:
    def __init__(self):
        self.handlers: Dict[str, Callable[[str, dict], None]] = {}

    def on(self, key: str, cb: Callable[[str, dict], None]):
        self.handlers[key] = cb

    async def run_binance(self, symbols):
        url = "wss://fstream.binance.com/stream"
        streams = []
        for s in symbols:
            b = to_binance(s).lower()
            streams += [f"{b}@trade", f"{b}@bookTicker"]
        params = {"method":"SUBSCRIBE","params": streams,"id":1}
        async with websockets.connect(url, max_size=2**23) as ws:
            await ws.send(json.dumps(params))
            async for msg in ws:
                d = json.loads(msg)
                if "stream" in d and "data" in d:
                    st = d["stream"]
                    data = d["data"]
                    if st.endswith("@trade"):
                        sym = data["s"]
                        self.handlers.get("trade", lambda *_: None)(sym, {"p": float(data["p"]), "q": float(data["q"]), "m": bool(data["m"])})
                    elif st.endswith("@bookTicker"):
                        sym = data["s"]
                        self.handlers.get("bt", lambda *_: None)(sym, {"b": float(data["b"]), "a": float(data["a"]), "B": float(data.get("B",0.0)), "A": float(data.get("A",0.0))})

    async def run_okx(self, symbols):
        if not os.getenv("WS_ENABLE_OKX","1") in ("1","true","t","yes","on"): return
        url = "wss://ws.okx.com:8443/ws/v5/public"
        subs = [{"channel": "trades", "instId": to_okx(s)} for s in symbols] +                [{"channel": "tickers","instId": to_okx(s)} for s in symbols]
        async with websockets.connect(url, max_size=2**23) as ws:
            await ws.send(json.dumps({"op":"subscribe","args":subs}))
            async for msg in ws:
                d = json.loads(msg)
                if d.get("event"): continue
                if d.get("arg",{}).get("channel")=="trades":
                    for t in d.get("data", []):
                        sym = d["arg"]["instId"]
                        self.handlers.get("trade", lambda *_: None)(sym, {"p": float(t["px"]), "q": float(t["sz"]), "m": (t["side"]=="sell")})
                elif d.get("arg",{}).get("channel")=="tickers":
                    for t in d.get("data", []):
                        sym = d["arg"]["instId"]
                        self.handlers.get("bt", lambda *_: None)(sym, {"b": float(t["bidPx"]), "a": float(t["askPx"]), "B": float(t.get("bidSz",0.0)), "A": float(t.get("askSz",0.0))})

    async def run_bybit(self, symbols):
        if not os.getenv("WS_ENABLE_BYBIT","1") in ("1","true","t","yes","on"): return
        url = "wss://stream.bybit.com/v5/public/linear"
        subs = [{"op":"subscribe","args":[f"publicTrade.{to_bybit(s)}", f"orderbook.1.{to_bybit(s)}"]}]
        async with websockets.connect(url, max_size=2**23) as ws:
            await ws.send(json.dumps(subs[0]))
            async for msg in ws:
                d = json.loads(msg)
                topic = d.get("topic","")
                if topic.startswith("publicTrade"):
                    for t in d.get("data", []):
                        sym = topic.split(".")[-1]
                        self.handlers.get("trade", lambda *_: None)(sym, {"p": float(t["p"]), "q": float(t["v"]), "m": (t["S"]=="Sell")})
                elif topic.startswith("orderbook.1"):
                    data = d.get("data", {})
                    sym = topic.split(".")[-1]
                    # best bid/ask
                    if "b" in data and "a" in data and data["b"] and data["a"]:
                        b = float(data["b"][0][0]); a = float(data["a"][0][0])
                        self.handlers.get("bt", lambda *_: None)(sym, {"b": b, "a": a, "B": 0.0, "A": 0.0})
