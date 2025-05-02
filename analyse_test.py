import ccxt
import pandas as pd
import pandas_ta as ta

exchange = ccxt.kucoinfutures()
markets = exchange.load_markets()
symbols = [s for s in markets if s.endswith(':USDTM')]

def fetch_ohlcv(symbol, timeframe="4h", limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print(f"Erreur sur {symbol}: {e}")
        return None

def analyze(df):
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    last = df.iloc[-1]

    if last["rsi"] > 70 and last["macd"] < last["macd_signal"]:
        return "SHORT"
    elif last["rsi"] < 30 and last["macd"] > last["macd_signal"]:
        return "LONG"
    return None

results = []

for symbol in symbols[:30]:  # Analyse 30 cryptos
    df = fetch_ohlcv(symbol)
    if df is not None:
        signal = analyze(df)
        if signal:
            results.append((symbol.replace(":USDTM", ""), signal))

print("Signaux détectés :")
for r in results:
    print(r)
