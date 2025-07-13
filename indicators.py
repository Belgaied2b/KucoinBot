import pandas as pd

def calculate_rsi(df, period=14):
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd_histogram(df, short=12, long=26, signal=9):
    short_ema = df["close"].ewm(span=short, adjust=False).mean()
    long_ema = df["close"].ewm(span=long, adjust=False).mean()
    macd_line = short_ema - long_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return histogram

def calculate_ma200(df):
    return df["close"].rolling(window=200).mean()

def calculate_atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

def calculate_fvg_zones(df, direction="long"):
    zones = []
    for i in range(2, len(df)):
        prev2 = df.iloc[i - 2]
        prev1 = df.iloc[i - 1]
        curr = df.iloc[i]

        if direction == "long":
            if prev2["low"] > curr["high"]:
                zones.append((curr["high"], prev2["low"]))
        elif direction == "short":
            if prev2["high"] < curr["low"]:
                zones.append((prev2["high"], curr["low"]))
    return zones

def detect_divergence(df, direction="long", window=20):
    """
    Détecte une divergence simple entre le prix et le RSI.
    - LONG : le prix fait un plus bas mais le RSI ne confirme pas
    - SHORT : le prix fait un plus haut mais le RSI ne confirme pas
    """
    if "rsi" not in df.columns:
        df["rsi"] = calculate_rsi(df)

    prices = df["close"].iloc[-window:]
    rsis = df["rsi"].iloc[-window:]

    if direction == "long":
        price_low = prices.idxmin()
        rsi_at_price_low = rsis.loc[price_low]

        next_low = prices[price_low:].idxmin()
        rsi_next = rsis.loc[next_low]

        return rsi_next > rsi_at_price_low
    else:
        price_high = prices.idxmax()
        rsi_at_price_high = rsis.loc[price_high]

        next_high = prices[price_high:].idxmax()
        rsi_next = rsis.loc[next_high]

        return rsi_next < rsi_at_price_high
