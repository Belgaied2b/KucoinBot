import asyncio, json, time
from collections import deque
import websockets

BINANCE_WS = "wss://fstream.binance.com/stream"

class InstitutionalState:
    __slots__=("price","cvd","delta","vol","funding","oi_now","oi_prev","liq_ema",
               "book_bid_vol","book_ask_vol","book_imbal","sweep_side","sweep_ts",
               "best_bid","best_ask","mid","spread")
    def __init__(self):
        self.price=None; self.cvd=0.0; self.delta=0.0; self.vol=0.0; self.funding=0.0
        self.oi_now=None; self.oi_prev=None; self.liq_ema=0.0
        self.book_bid_vol=0.0; self.book_ask_vol=0.0; self.book_imbal=0.0
        self.sweep_side="NONE"; self.sweep_ts=0
        self.best_bid=None; self.best_ask=None; self.mid=None; self.spread=None

class InstitutionalAggregator:
    def __init__(self, symbol: str, w_cfg):
        self.symbol=symbol.lower()
        self.trades=deque(maxlen=3000)
        self.state=InstitutionalState()
        self.w=w_cfg  # (w_oi, w_funding, w_delta, w_liq, w_book_imbal)

    def _score_oi(self):
        s=self.state
        if s.oi_now is None or s.oi_prev is None or s.oi_prev<=0: return 0.0
        d=(s.oi_now-s.oi_prev)/s.oi_prev
        return max(0.0, min(1.0, abs(d)/0.05))
    def _score_funding(self): return max(0.0, min(1.0, abs(self.state.funding)/0.05))
    def _score_delta(self):
        vol=sum(abs(q) for _,q,is_buy,_ in self.trades)
        if vol<=0: return 0.0
        delta=sum((q if is_buy else -q) for _,q,is_buy,_ in self.trades)
        return max(0.0, min(1.0, abs(delta/vol)))
    def _score_liq(self): return max(0.0, min(1.0, self.state.liq_ema/1_000_000))
    def _score_book(self):
        b,a=self.state.book_bid_vol,self.state.book_ask_vol; tot=b+a
        if tot<=0: return 0.0
        return abs(b-a)/tot

    def get_meta_score(self):
        w_oi,w_f,w_d,w_l,w_b=self.w
        s_oi=self._score_oi(); s_f=self._score_funding(); s_d=self._score_delta()
        s_l=self._score_liq(); s_b=self._score_book()
        st=self.state
        score=w_oi*s_oi+w_f*s_f+w_d*s_d+w_l*s_l+w_b*s_b
        return round(score,3),{
            "price": st.price,
            "oi_score": round(s_oi,3),
            "funding": st.funding, "funding_score": round(s_f,3),
            "delta_score": round(s_d,3), "cvd": round(st.cvd,3),
            "liq_score": round(s_l,3),
            "book_imbal_score": round(s_b,3),
            "sweep_side": st.sweep_side,
            "best_bid": st.best_bid, "best_ask": st.best_ask, "mid": st.mid, "spread": st.spread
        }

    async def run(self):
        streams=[f"{self.symbol}@trade", f"{self.symbol}@openInterest@1s",
                 f"{self.symbol}@markPrice@1s", f"{self.symbol}@forceOrder", f"{self.symbol}@depth5@100ms"]
        url=f"{BINANCE_WS}?streams={'/'.join(streams)}"
        oi_prev=None
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5):
            try:
                async for raw in ws:
                    d=json.loads(raw).get("data",{}); e=d.get("e")

                    if e=="trade":
                        p=float(d["p"]); q=float(d["q"]); m=d["m"]; t=d["T"]
                        is_buy=(m is False)
                        self.trades.append((p,q,is_buy,t))
                        st=self.state
                        st.price=p
                        st.delta=sum((x[1] if x[2] else -x[1]) for x in self.trades)
                        st.vol=sum(abs(x[1]) for x in self.trades)
                        st.cvd += (q if is_buy else -q)

                    if e=="openInterest":
                        now=float(d["oi"]); self.state.oi_prev=oi_prev if oi_prev is not None else now
                        self.state.oi_now=now; oi_prev=now

                    if e=="markPriceUpdate":
                        fr=float(d.get("r",0.0)); 
                        if fr!=0.0: self.state.funding=fr

                    if e=="forceOrder":
                        od=d.get("o",{}); notional=float(od.get("q",0.0))*float(od.get("ap",0.0))
                        self.state.liq_ema=0.7*self.state.liq_ema+0.3*notional

                    if d.get("e") is None and "b" in d:
                        bids=d["b"][:5]; asks=d["a"][:5]
                        bb = float(bids[0][0]) if bids else None
                        ba = float(asks[0][0]) if asks else None
                        st=self.state
                        st.best_bid=bb; st.best_ask=ba
                        if bb and ba:
                            st.mid=(bb+ba)/2.0
                            st.spread=ba-bb
                        bvol=sum(float(x[1]) for x in bids); avol=sum(float(x[1]) for x in asks)
                        pb,pa=st.book_bid_vol,st.book_ask_vol
                        st.book_bid_vol=0.7*pb+0.3*bvol; st.book_ask_vol=0.7*pa+0.3*avol
                        now=time.time()
                        if pb>0 and bvol<0.5*pb: st.sweep_side="BID"; st.sweep_ts=now
                        elif pa>0 and avol<0.5*pa: st.sweep_side="ASK"; st.sweep_ts=now
                        elif now-st.sweep_ts>2.0: st.sweep_side="NONE"
            except Exception:
                await asyncio.sleep(1.0)
                continue
