import pandas as pd

def detect_bos_cos_choch(df, direction="long"):
    bos, cos, choch = False, False, False

    if df is None or len(df) < 50 or not all(k in df.columns for k in ['high', 'low', 'close']):
        return bos, cos, choch

    try:
        df = df.copy()
        df['high'] = pd.to_numeric(df['high'], errors='coerce')
        df['low'] = pd.to_numeric(df['low'], errors='coerce')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df.dropna(subset=['high', 'low', 'close'], inplace=True)

        # Travail sur les 30 dernières bougies
        lookback = 30
        recent = df[-lookback:]

        swing_high = recent['high'].max()
        swing_low = recent['low'].min()

        last_close = recent['close'].iloc[-1]
        prev_close = recent['close'].iloc[-2]
        last_high = recent['high'].iloc[-1]
        last_low = recent['low'].iloc[-1]

        # 🔹 BOS : cassure du plus haut/bas récent
        if direction == "long" and last_close >= swing_high * 0.995:
            bos = True
        elif direction == "short" and last_close <= swing_low * 1.005:
            bos = True

        # 🔹 COS : repli dans structure
        if direction == "long" and last_low <= swing_low and last_close < prev_close:
            cos = True
        elif direction == "short" and last_high >= swing_high and last_close > prev_close:
            cos = True

        # 🔹 CHoCH : inversion locale de tendance
        if direction == "long":
            choch = last_close > df['close'].iloc[-5] and last_low > df['low'].iloc[-5]
        else:
            choch = last_close < df['close'].iloc[-5] and last_high < df['high'].iloc[-5]

    except Exception as e:
        print(f"⚠️ Erreur structure (BOS/COS/CHoCH) : {e}")

    return bos, cos, choch
