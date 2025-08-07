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
        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        if not isinstance(data, list):
            raise ValueError(f"Données invalides (Open Interest): {data}")

        df = pd.DataFrame(data)
        if "sumOpenInterest" in df.columns:
            df["sumOpenInterest"] = pd.to_numeric(df["sumOpenInterest"], errors='coerce')
        else:
            raise KeyError("sumOpenInterest absent des données")

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.dropna(subset=["sumOpenInterest"])
        return df
    except Exception as e:
        print(f"[{symbol}] Erreur Open Interest : {e}")
        return pd.DataFrame()

# 💰 Funding Rate Binance
def fetch_binance_funding_rate(symbol="BTCUSDT", limit=100):
    try:
        url = f"{BINANCE_BASE}/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        if not isinstance(data, list):
            raise ValueError(f"Données invalides (Funding Rate): {data}")

        df = pd.DataFrame(data)
        if "fundingRate" in df.columns:
            df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors='coerce')
        else:
            raise KeyError("fundingRate absent des données")

        df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
        df = df.dropna(subset=["fundingRate"])
        return df
    except Exception as e:
        print(f"[{symbol}] Erreur Funding Rate : {e}")
        return pd.DataFrame()

# 💥 Liquidations Binance
def fetch_binance_liquidations(symbol="BTCUSDT"):
    try:
        url = f"{BINANCE_BASE}/fapi/v1/allForceOrders"
        params = {"symbol": symbol, "limit": 200}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[{symbol}] Erreur liquidations Binance : {e}")
        return []

# 💣 Spike de liquidations
def compute_liquidation_spike(liqs, threshold_usd=50000):
    try:
        if not isinstance(liqs, list):
            return False
        recent_liqs = liqs[-10:] if len(liqs) >= 10 else liqs
        for order in recent_liqs:
            qty = float(order.get("origQty", 0) or 0)
            price = float(order.get("price", 0) or 0)
            if qty * price >= threshold_usd:
                return True
    except Exception as e:
        print(f"Erreur dans compute_liquidation_spike : {e}")
    return False

# 📊 CVD (Cumulative Volume Delta)
def compute_cvd(df):
    try:
        if df is None or len(df) < 10:
            return False
        df = df.copy()
        df["delta"] = df["close"] - df["open"]
        df["cvd"] = df["delta"].cumsum()
        return df["cvd"].iloc[-1] > df["cvd"].iloc[-5]
    except Exception as e:
        print(f"Erreur CVD : {e}")
        return False

# 🧠 Score institutionnel global
def get_institutional_score(df_binance, symbol_binance="BTCUSDT"):
    # ✅ Corriger les symboles pour Binance si nécessaire
    symbol_binance = symbol_binance.replace("USDTM", "USDT").replace("XBT", "BTC")

    open_interest_df = fetch_binance_open_interest(symbol_binance)
    funding_df = fetch_binance_funding_rate(symbol_binance)
    liquidations = fetch_binance_liquidations(symbol_binance)
    cvd_ok = compute_cvd(df_binance)

    score = 0
    details = []

    # 🔍 Open Interest
    if not open_interest_df.empty:
        try:
            if open_interest_df["sumOpenInterest"].iloc[-1] > open_interest_df["sumOpenInterest"].iloc[-5]:
                score += 1
                details.append("OI↑")
        except Exception as e:
            print(f"[{symbol_binance}] Erreur OI check : {e}")
    else:
        print(f"[{symbol_binance}] Erreur Open Interest : 'sumOpenInterest absent des données'")

    # 🔍 Funding Rate
    if not funding_df.empty:
        try:
            if funding_df["fundingRate"].iloc[-1] <= 0.0001:
                score += 1
                details.append("Funding OK")
        except Exception as e:
            print(f"[{symbol_binance}] Erreur Funding check : {e}")
    else:
        print(f"[{symbol_binance}] Erreur Funding Rate : 'fundingRate absent des données'")

    # 🔍 Liquidations
    if compute_liquidation_spike(liquidations):
        score += 1
        details.append("Liq Spike")

    # 🔍 CVD
    if cvd_ok:
        score += 1
        details.append("CVD OK")

    return score, details
