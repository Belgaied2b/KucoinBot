import time, json, os
from dataclasses import dataclass, field
from typing import Dict, Optional
from config import SETTINGS

@dataclass
class PositionState:
    side: str
    entry: float
    qty_value: float
    sl: float
    tp1: float
    tp2: float
    tp1_done: bool = False
    open_time: float = field(default_factory=time.time)

@dataclass
class PendingOrder:
    oid: str
    symbol: str
    side: str
    price: float
    ts_open: float
    filled_value: float = 0.0
    avg_fill_price: Optional[float] = None
    status: str = "open"

class OrderManager:
    def __init__(self):
        self.pos: Dict[str, PositionState] = {}
        self.pending: Dict[str, PendingOrder] = {}
        self.pending_by_symbol: Dict[str, str] = {}
        self.persist_path=SETTINGS.persist_path
        self._load()

    def _load(self):
        if os.path.exists(self.persist_path):
            try:
                data=json.load(open(self.persist_path,"r"))
                for sym,p in data.get("positions",{}).items():
                    self.pos[sym]=PositionState(**p)
            except: pass

    def _save(self):
        try:
            json.dump({"positions":{k:vars(v) for k,v in self.pos.items()}}, open(self.persist_path,"w"))
        except: pass

    def open_position(self, symbol: str, side: str, entry: float, sl: float, tp1: float, tp2: float):
        self.pos[symbol]=PositionState(side=side, entry=entry, qty_value=SETTINGS.margin_per_trade, sl=sl, tp1=tp1, tp2=tp2)
        self._save()

    def update_entry_with_fill(self, symbol: str, avg_fill_price: float):
        p=self.pos.get(symbol)
        if not p: return
        p.entry = avg_fill_price
        self._save()

    def close_half_at_tp1(self, symbol: str):
        p=self.pos.get(symbol)
        if not p or p.tp1_done: return
        p.tp1_done=True
        if SETTINGS.breakeven_after_tp1: p.sl=p.entry
        self._save()

    def close_all(self, symbol: str, reason: str = ""):
        if symbol in self.pos:
            del self.pos[symbol]; self._save()

    def add_pending(self, oid: str, symbol: str, side: str, price: float):
        self.pending[oid]=PendingOrder(oid=oid, symbol=symbol, side=side, price=price, ts_open=time.time())
        self.pending_by_symbol[symbol]=oid
    def set_pending_status(self, oid: str, status: str, avg_fill_price: Optional[float]=None, filled_value: Optional[float]=None):
        po=self.pending.get(oid)
        if not po: return
        po.status=status
        if avg_fill_price is not None: po.avg_fill_price = avg_fill_price
        if filled_value is not None: po.filled_value = filled_value
    def remove_pending(self, oid: str):
        po=self.pending.pop(oid, None)
        if po and self.pending_by_symbol.get(po.symbol)==oid:
            del self.pending_by_symbol[po.symbol]
