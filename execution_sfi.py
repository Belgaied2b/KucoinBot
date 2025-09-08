# -*- coding: utf-8 -*-
"""
execution_sfi.py — Smart Fill Infrastructure
- Entrées "pegged-to-mid" en N tranches, post-only par défaut
- Re-quote si position de file (queue position) devient défavorable
- Microstructure: microprice, tick ladder, maker→taker switch contrôlé (IOC fallback)
"""

from __future__ import annotations
import os, time, math, uuid, logging
from typing import Optional, Tuple, List, Dict, Any

POST_ONLY_DEFAULT = os.environ.get("POST_ONLY_DEFAULT", "1") == "1"
PEGMID_SPLIT = [float(x) for x in os.environ.get("PEGMID_SPLIT", "0.6,0.4").split(",")]
TICK_DEFAULT = float(os.environ.get("TICK_DEFAULT", "0.01"))
QUEUE_THRESHOLD = float(os.environ.get("QUEUE_THRESHOLD", "2000"))
REQUOTE_COOLDOWN_MS = int(os.environ.get("REQUOTE_COOLDOWN_MS", "800"))
MAKER_TO_TAKER_SWITCH = os.environ.get("MAKER_TO_TAKER_SWITCH", "0") == "1"

try:
    import kucoin_adapter as kt
except Exception:
    try:
        import kucoin_trader as kt  # type: ignore
    except Exception:
        kt = None
        logging.warning("execution_sfi: aucun backend KuCoin importable.")

def _side_to_kucoin(side: str) -> str:
    return "buy" if side.lower() == "long" else "sell"

def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    if kt and hasattr(kt, "get_symbol_meta"):
        try:
            m = kt.get_symbol_meta(symbol)
            if isinstance(m, dict):
                return m
        except Exception:
            pass
    return {"priceIncrement": TICK_DEFAULT}

def get_orderbook_top(symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if kt and hasattr(kt, "get_orderbook_top"):
        try:
            top = kt.get_orderbook_top(symbol)
            if isinstance(top, dict):
                bid = float(top.get("bestBid")) if top.get("bestBid") is not None else None
                ask = float(top.get("bestAsk")) if top.get("bestAsk") is not None else None
                bidSz = top.get("bidSize"); askSz = top.get("askSize")
                bidSz = float(bidSz) if bidSz is not None else None
                askSz = float(askSz) if askSz is not None else None
                return bid, ask, bidSz, askSz
        except Exception:
            pass
    return None, None, None, None

def place_limit(symbol: str, side: str, price: float, value_usdt: float,
                sl: Optional[float]=None, tp1: Optional[float]=None, tp2: Optional[float]=None,
                post_only: bool = POST_ONLY_DEFAULT, client_order_id: Optional[str]=None) -> Dict[str, Any]:
    if kt is None:
        raise RuntimeError("execution_sfi: backend KuCoin indisponible")
    if client_order_id is None:
        client_order_id = f"sig_{uuid.uuid4().hex[:10]}"

    def _try(fn, **kwargs):
        try:
            return fn(**kwargs)
        except TypeError:
            kwargs.pop("post_only", None)
            kwargs.pop("extra_kwargs", None)
            return fn(**kwargs)

    if hasattr(kt, "place_limit_order"):
        return _try(kt.place_limit_order, symbol=symbol, side=_side_to_kucoin(side),
                    price=price, value_usdt=value_usdt, sl=sl, tp1=tp1, tp2=tp2,
                    post_only=post_only, client_order_id=client_order_id, extra_kwargs={})
    if hasattr(kt, "place_order"):
        return _try(kt.place_order, symbol=symbol, side=_side_to_kucoin(side),
                    order_type="limit", price=price, value_usdt=value_usdt,
                    sl=sl, tp1=tp1, tp2=tp2, post_only=post_only,
                    client_order_id=client_order_id, extra_kwargs={})
    if hasattr(kt, "place_limit_valueqty"):
        return _try(kt.place_limit_valueqty, symbol=symbol, side=_side_to_kucoin(side),
                    price=price, value_usdt=value_usdt, sl=sl, tp1=tp1, tp2=tp2,
                    post_only=post_only, client_order_id=client_order_id, extra_kwargs={})
    raise RuntimeError("execution_sfi: aucune méthode LIMIT compatible")

def cancel_order(order_id: str) -> Any:
    if kt and hasattr(kt, "cancel_order"):
        return kt.cancel_order(order_id)
    raise RuntimeError("cancel_order indisponible")

def replace_order(order_id: str, new_price: float) -> Any:
    if kt and hasattr(kt, "replace_order"):
        try:
            return kt.replace_order(order_id, new_price)
        except TypeError:
            return kt.replace_order(order_id=order_id, new_price=new_price)
    try:
        cancel_order(order_id)
    except Exception:
        pass
    return {"replaced": False, "cancelled": True}

def get_order_status(order_id: str) -> Dict[str, Any]:
    if kt and hasattr(kt, "get_order_status"):
        return kt.get_order_status(order_id)
    return {"status": "unknown"}

def microprice(bid: Optional[float], ask: Optional[float], bid_sz: Optional[float], ask_sz: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    if not bid_sz or not ask_sz:
        return (bid + ask) / 2.0
    return (ask * bid_sz + bid * ask_sz) / (bid_sz + ask_sz)

def passive_price(side: str, mid: float, tick: float, bid: Optional[float], ask: Optional[float]) -> float:
    s = side.lower()
    if s == "long":
        return max(0.0, (bid if bid else mid) - tick)
    return max(0.0, (ask if ask else mid) + tick)

def estimate_queue_position(symbol: str, side: str, price: float) -> float:
    if kt and hasattr(kt, "get_orderbook_levels"):
        try:
            ob = kt.get_orderbook_levels(symbol, depth=5)  # type: ignore
            vol_ahead = 0.0
            s = side.lower()
            for lvl in ob:
                p = float(lvl.get("price", 0)); sz = float(lvl.get("size", 0))
                sd = str(lvl.get("side", "")).lower()
                if s == "long" and sd == "sell":
                    if p < price: continue
                    if abs(p - price) < 1e-12: break
                if s == "short" and sd == "buy":
                    if p > price: continue
                    if abs(p - price) < 1e-12: break
                vol_ahead += sz
            return vol_ahead
        except Exception:
            pass
    return float("+inf")

class SFIEngine:
    def __init__(self, symbol: str, side: str, total_value_usdt: float,
                 sl: Optional[float], tp1: Optional[float], tp2: Optional[float]):
        self.symbol = symbol
        self.side = side.lower()
        self.total_value = total_value_usdt
        self.sl, self.tp1, self.tp2 = sl, tp1, tp2
        self.tick = float(get_symbol_meta(symbol).get("priceIncrement", TICK_DEFAULT))
        self.order_ids: List[str] = []
        self.last_requote_ms = 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _quote_prices(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        bid, ask, bid_sz, ask_sz = get_orderbook_top(self.symbol)
        if bid is None or ask is None:
            return None, None, None
        mid = (bid + ask) / 2.0
        base = passive_price(self.side, mid, self.tick, bid, ask)
        if self.side == "long":
            p1, p2 = base, max(0.0, base - self.tick)
        else:
            p1, p2 = base, base + self.tick
        return p1, p2, microprice(bid, ask, bid_sz, ask_sz)

    def _place_tranche(self, price: float, value: float) -> Optional[str]:
        """
        Place une tranche LIMIT via backend KuCoin.
        ✅ Retourne uniquement un **vrai** orderId (exchange).
        ❌ N'utilise plus clientOid/uuid en secours (évite faux positifs).
        """
        try:
            resp = place_limit(
                self.symbol, self.side, price, value,
                self.sl, self.tp1, self.tp2,
                post_only=POST_ONLY_DEFAULT
            )
            if not isinstance(resp, dict):
                logging.info("[%s] tranche refusée (bad resp type): %s", self.symbol, resp)
                return None

            data = resp.get("data") or {}
            code = resp.get("code")
            msg  = resp.get("msg")
            order_id = resp.get("orderId") or data.get("orderId")

            if order_id:
                return str(order_id)

            # Pas d'orderId -> on log le code/message pour debug et on renvoie None
            logging.info("[%s] tranche refusée: code=%s msg=%s data=%s",
                         self.symbol, code, msg, {k: data.get(k) for k in ("clientOid", "orderId")})
            return None
        except Exception as e:
            logging.error("[%s] tranche place KO: %s", self.symbol, e)
            return None

    def place_initial(self, entry_hint: Optional[float] = None) -> List[str]:
        p1, p2, _ = self._quote_prices()
        splits = PEGMID_SPLIT if len(PEGMID_SPLIT) >= 2 else [0.6, 0.4]
        sizes = [max(0.0, self.total_value * s) for s in splits[:2]]
        orders = []
        if p1 is not None and p2 is not None:
            for price, val in zip([p1, p2], sizes):
                oid = self._place_tranche(price, val)
                if oid:
                    orders.append(oid)
        elif entry_hint is not None:
            oid = self._place_tranche(entry_hint, self.total_value)
            if oid: orders.append(oid)
        self.order_ids = orders
        return orders

    def maybe_requote(self) -> None:
        now = self._now_ms()
        if now - self.last_requote_ms < REQUOTE_COOLDOWN_MS:
            return
        if not self.order_ids:
            return
        p1, _p2, _micro = self._quote_prices()
        if p1 is None:
            return
        qpos = estimate_queue_position(self.symbol, self.side, p1)
        if qpos <= QUEUE_THRESHOLD:
            return
        new_price = p1
        for oid in list(self.order_ids):
            try:
                replace_order(oid, new_price)  # type: ignore
            except Exception:
                try:
                    cancel_order(oid)  # type: ignore
                except Exception:
                    pass
        self.last_requote_ms = now

    def maker_to_taker_small(self, pct: float = 0.25, slippage_ticks: int = 1) -> Optional[str]:
        if not MAKER_TO_TAKER_SWITCH or pct <= 0:
            return None
        try:
            if hasattr(kt, "place_market_by_value"):
                return str(kt.place_market_by_value(self.symbol, self.side, self.total_value * pct).get("orderId"))
        except Exception:
            pass
        return None
