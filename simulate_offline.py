
# simulate_offline.py — E2E offline dry-run (no network)
import numpy as np, pandas as pd

import analyze_bridge as bridge
import execution_sfi as sfi
from risk_sizing import valueqty_from_risk

# ---- Mock KuCoin backend for SFI ----
class _MockKC:
    def __init__(self): self.oid = 0
    def get_symbol_meta(self, symbol): return {"priceIncrement": 0.01}
    def get_orderbook_top(self, symbol):
        price = 100.0
        return {"bestBid": price-0.01, "bestAsk": price+0.01, "bidSize": 100, "askSize": 100}
    def place_limit_valueqty(self, symbol, side, price, value_usdt, sl=None, tp1=None, tp2=None, post_only=True, client_order_id=None, extra_kwargs=None):
        self.oid += 1; return {"status": "ok", "orderId": f"mock{self.oid}", "price": price, "valueQty": value_usdt}
    def cancel(self, order_id): return {"status": "cancelled", "orderId": order_id}
    def place_market_by_value(self, symbol, side, valueQty):
        self.oid += 1; return {"status": "ok", "orderId": f"mockmkt{self.oid}", "valueQty": valueQty}

sfi.kt = _MockKC()

def make_df(n=300, trend=0.05):
    t = np.arange(n)
    price = 100 + trend * t + np.random.normal(0, 0.5, n).cumsum() * 0.01
    high = price + np.abs(np.random.normal(0, 0.2, n))
    low  = price - np.abs(np.random.normal(0, 0.2, n))
    vol  = np.random.randint(1000, 2000, n)
    return pd.DataFrame({"open": price, "high": high, "low": low, "close": price, "volume": vol})

df_h1 = make_df(500, trend=0.03)
df_h4 = make_df(400, trend=0.02)
inst = {"score": 3.0}
macro = {"TOTAL": 1.0}

res = bridge.analyze_signal(symbol="BTCUSDT", df_h1=df_h1, df_h4=df_h4, institutional=inst, macro=macro)
print("Analyze:", {k: res.get(k) for k in ["side","entry","sl","tp1","tp2","score"]})

entry, sl = res["entry"], res["sl"]
valueqty = valueqty_from_risk(entry, sl, 5.0)
eng = sfi.SFIEngine("BTCUSDT", res["side"], valueqty, res["sl"], res["tp1"], res["tp2"])
oids = eng.place_initial(entry_hint=entry)
eng.maybe_requote()
print("OK:", oids)
