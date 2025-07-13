def detect_bos_cos_choch(df, direction="long"):
    bos, cos, choch = False, False, False

    # Vérification de base
    if df is None or len(df) < 10 or not all(k in df.columns for k in ['high', 'low', 'close']):
        return bos, cos, choch

    try:
        df = df.copy()
        df['high'] = pd.to_numeric(df['high'], errors='coerce')
        df['low'] = pd.to_numeric(df['low'], errors='coerce')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')

        df = df.dropna(subset=['high', 'low', 'close'])

        if len(df) < 10:
            return bos, cos, choch

        highs = df["high"].rolling(window=5, min_periods=1).max()
        lows = df["low"].rolling(window=5, min_periods=1).min()

        recent_high = highs.iloc[-2]
        recent_low = lows.iloc[-2]
        current_high = df["high"].iloc[-1]
        current_low = df["low"].iloc[-1]
        prev_close = df["close"].iloc[-2]
        current_close = df["close"].iloc[-1]

        # BOS
        if direction == "long" and current_close > recent_high:
            bos = True
        elif direction == "short" and current_close < recent_low:
            bos = True

        # COS
        if direction == "long" and current_low < recent_low and current_close < prev_close:
            cos = True
        elif direction == "short" and current_high > recent_high and current_close > prev_close:
            cos = True

        # CHoCH
        if direction == "long":
            choch = df["low"].iloc[-1] > df["low"].iloc[-5] and df["close"].iloc[-1] > df["close"].iloc[-5]
        else:
            choch = df["high"].iloc[-1] < df["high"].iloc[-5] and df["close"].iloc[-1] < df["close"].iloc[-5]

    except Exception as e:
        print(f"⚠️ Erreur structure (BOS/COS/CHoCH) : {e}")

    return bos, cos, choch
