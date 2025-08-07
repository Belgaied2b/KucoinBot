import requests
import pandas as pd

BINANCE_BASE = "https://fapi.binance.com"

# ⏱️ Open Interest Binance
def fetch_binance_open_interest(symbol="BTCUSDT", interval="5m", limit=50):
    try:
        url = f"{BINANCE_BASE}/futures/data/openInterestHist"
        params = {
            "symbol": symbol,
            "period": interval,
            "limit": limit
        }
        r = requests.get(url, params=params)
        data = r.json()

        df = pd.DataFrame(data)
        df["sumOpenInterest"] = pd.to_numeric(df["sumOpenInterest"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print(f"[{symbol}] Erreur Open Interest : {e}")
        return pd.DataFrame()

# 💰 Funding Rate Binance
def fetch_binance_funding_rate(symbol="BTCUSDT", limit=100):
    try:
        url = f"{BINANCE_BASE}/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": limit}
        r = requests.get(url, params=params)
        data = r.json()

        df = pd.DataFrame(data)
        df["fundingRate"] = pd.to_numeric(df["fundingRate"])
        df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
        return df
    except Exception as e:
        print(f"[{symbol}] Erreur Funding Rate : {e}")
        return pd.DataFrame()

# 💥 Liquidations Binance
def fetch_binance_liquidations(symbol):
    try:
        url = f"{BINANCE_BASE}/fapi/v1/allForceOrders?symbol={symbol}&limit=200"
        r = requests.get(url)
        return r.json()
    except Exception as e:
        print(f"[{symbol}] Erreur liquidations Binance : {e}")
        return []

def compute_liquidation_spike(liqs, threshold_usd=50000):
    for order in liqs[-10:]:
        qty = float(order.get("origQty", 0))
        price = float(order.get("price", 0))
        if qty * price >= threshold_usd:
            return True
    return False

# 📊 CVD (Cumulative Volume Delta) simple proxy
def compute_cvd(df):
    try:
        df["delta"] = df["close"] - df["open"]
        cvd = df["delta"].cumsum()
        return cvd.iloc[-1] > cvd.iloc[-5]
    except Exception as e:
        print(f"Erreur CVD : {e}")
        return False

# 🧠 Score institutionnel global
def get_institutional_score(df, symbol_binance="BTCUSDT"):
    open_interest_df = fetch_binance_open_interest(symbol_binance)
    funding_df = fetch_binance_funding_rate(symbol_binance)
    liquidations = fetch_binance_liquidations(symbol_binance)
    cvd_ok = compute_cvd(df)

    score = 0
    details = []

    if not open_interest_df.empty and open_interest_df["sumOpenInterest"].iloc[-1] > open_interest_df["sumOpenInterest"].iloc[-5]:
        score += 1
        details.append("OI↑")

    if not funding_df.empty and funding_df["fundingRate"].iloc[-1] <= 0.0001:
        score += 1
        details.append("Funding OK")

    if compute_liquidation_spike(liquidations):
        score += 1
        details.append("Liq Spike")

    if cvd_ok:
        score += 1
        details.append("CVD OK")

    return score, details
