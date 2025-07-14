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

        # Marge pour structure locale
        window = 20
        recent = df[-window:]

        highest_high = recent['high'].max()
        lowest_low = recent['low'].min()

        close_now = recent['close'].iloc[-1]
        close_prev = recent['close'].iloc[-2]
        high_now = recent['high'].iloc[-1]
        low_now = recent['low'].iloc[-1]

        # BOS (Break of Structure)
        if direction == "long" and close_now > highest_high * 0.995:
            bos = True
        elif direction == "short" and close_now < lowest_low * 1.005:
            bos = True

        # COS (Change of Structure, retour vers support)
        if direction == "long" and low_now < lowest_low and close_now < close_prev:
            cos = True
        elif direction == "short" and high_now > highest_high and close_now > close_prev:
            cos = True

        # CHoCH (Change of Character)
        close_5 = df['close'].iloc[-5]
        if direction == "long":
            choch = close_now > close_5 and low_now > df['low'].iloc[-5]
        else:
            choch = close_now < close_5 and high_now < df['high'].iloc[-5]

    except Exception as e:
        print(f"⚠️ Erreur structure (BOS/COS/CHoCH) : {e}")

    return bos, cos, choch
