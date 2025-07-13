import requests
import pandas as pd

def fetch_market_data():
    urls = {
        "BTC": "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=2&interval=hourly",
        "TOTAL": "https://api.coingecko.com/api/v3/global",
        "BTC.D": "https://api.coingecko.com/api/v3/global"
    }

    data = {}

    # BTC price
    try:
        btc_data = requests.get(urls["BTC"]).json()
        df = pd.DataFrame(btc_data["prices"], columns=["timestamp", "price"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        data["BTC"] = df
    except:
        data["BTC"] = pd.DataFrame()

    # TOTAL market cap
    try:
        total_data = requests.get(urls["TOTAL"]).json()
        total_usd = total_data["data"]["total_market_cap"]["usd"]
        total_pct_change = total_data["data"]["market_cap_change_percentage_24h_usd"]
        data["TOTAL"] = {"usd": total_usd, "change_pct": total_pct_change}
    except:
        data["TOTAL"] = {"usd": 0, "change_pct": 0}

    # BTC.D
    try:
        btc_d = requests.get(urls["BTC.D"]).json()
        btc_d_value = btc_d["data"]["market_cap_percentage"]["btc"]
        data["BTC.D"] = {"value": btc_d_value}
    except:
        data["BTC.D"] = {"value": 0}

    return data

def load_macro_data():
    market = fetch_market_data()
    return {
        "BTC": market["BTC"],
        "TOTAL": market["TOTAL"],
        "BTC.D": market["BTC.D"]
    }

def check_market_conditions(direction, btc_df, total_obj, btcd_obj):
    total_change = total_obj["change_pct"]
    btc_d_value = btcd_obj["value"]

    # Logiques intelligentes
    if direction == "long":
        total_ok = total_change > 0
        btc_d_trend = "HAUSSIER" if btc_d_value > 50 else "BAISSIER"
        total_trend = "⬆️" if total_ok else "⬇️"
    else:
        total_ok = total_change < 0
        btc_d_trend = "BAISSIER" if btc_d_value < 50 else "HAUSSIER"
        total_trend = "⬇️" if total_ok else "⬆️"

    return total_ok, btc_d_trend, total_trend
