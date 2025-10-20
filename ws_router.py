import os, asyncio, time
from typing import AsyncIterator, Dict, Any, List

WS_POLL_SEC = float(os.getenv("WS_POLL_SEC", "5"))
WS_FORCE_ALWAYS = os.getenv("WS_FORCE_ALWAYS", "1").lower() in ("1","true","t","yes","on")

class PollingSource:
    def __init__(self, symbols: List[str], interval_sec: float = WS_POLL_SEC):
        self.symbols = symbols
        self.interval = interval_sec
    async def __aiter__(self) -> AsyncIterator[Dict[str, Any]]:
        while True:
            now = time.time()
            for s in self.symbols:
                yield {"type": "bar" if WS_FORCE_ALWAYS else "tick", "symbol": s, "ts": now}
            await asyncio.sleep(self.interval)

class EventBus:
    def __init__(self):
        self._sources: List[AsyncIterator[Dict[str,Any]]] = []
    def add_source(self, it: AsyncIterator[Dict[str,Any]]): self._sources.append(it)
    async def start(self): pass
    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        iters = [s.__aiter__() for s in self._sources]
        while True:
            for it in iters:
                try:
                    yield await it.__anext__()
                except (StopIteration, StopAsyncIteration):
                    return
