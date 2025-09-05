# -*- coding: utf-8 -*-
"""
ws_router.py — Event bus + source de polling optimisée
- N'émet un 'bar' que si la dernière bougie 1h change (réduction d'events)
"""

from __future__ import annotations
import asyncio, time, logging
from typing import Dict, Any, List, AsyncGenerator, Optional
from kucoin_utils import fetch_klines

try:
    import kucoin_adapter as kt  # si dispo
except Exception:
    kt = None

class EventBus:
    def __init__(self):
        self._sources: List[AsyncGenerator] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    def add_source(self, gen: AsyncGenerator):
        self._sources.append(gen)

    async def start(self):
        async def _runner(gen):
            try:
                async for ev in gen:
                    await self._queue.put(ev)
            except Exception as e:
                logging.error("ws_router source stopped: %s", e)
        for gen in self._sources:
            asyncio.create_task(_runner(gen))

    async def events(self):
        while True:
            ev = await self._queue.get()
            yield ev

class PollingSource:
    def __init__(self, symbols: List[str], interval_sec: int = 5):
        self.symbols = symbols
        self.interval = interval_sec
        self._last_bar_ts: Dict[str, int] = {}  # time(ms) de la dernière 1h connue

    async def __aiter__(self):
        while True:
            t0 = time.time()
            for sym in self.symbols:
                # 1) Event 'top' (si backend dispo)
                if kt and hasattr(kt, "get_orderbook_top"):
                    try:
                        top = kt.get_orderbook_top(sym)
                        if isinstance(top, dict):
                            yield {"type": "top", "symbol": sym, "top": top, "ts": time.time()}
                    except Exception:
                        pass

                # 2) Event 'bar' UNIQUEMENT si nouvelle bougie 1h détectée
                try:
                    df = fetch_klines(sym, interval="1h", limit=2)
                    if df is not None and len(df) >= 1:
                        last = df.iloc[-1].to_dict()
                        last_ts = int(last.get("time", 0))
                        prev = self._last_bar_ts.get(sym, 0)
                        if last_ts > prev:
                            self._last_bar_ts[sym] = last_ts
                            yield {"type": "bar", "symbol": sym, "bar": last, "ts": time.time()}
                except Exception:
                    pass

                await asyncio.sleep(0)  # cooperative

            dt = time.time() - t0
            await asyncio.sleep(max(0.0, self.interval - dt))
