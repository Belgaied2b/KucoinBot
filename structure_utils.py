def detect_bos_cos_choch(df, direction="long"):
    bos, cos, choch = False, False, False

    if df is None or len(df) < 10 or not all(k in df.columns for k in ['high', 'low', 'close']):
        return bos, cos, choch  # Renvoie tout à False si pas assez de données

    # Calcul des plus hauts et plus bas récents
    highs = df["high"].rolling(window=5, min_periods=1).max()
    lows = df["low"].rolling(window=5, min_periods=1).min()

    try:
        recent_high = highs.iloc[-2]
        recent_low = lows.iloc[-2]
        current_high = df["high"].iloc[-1]
        current_low = df["low"].iloc[-1]
        prev_close = df["close"].iloc[-2]
        current_close = df["close"].iloc[-1]

        # BOS = cassure du plus haut/bas récent avec clôture dans le sens du trade
        if direction == "long" and current_close > recent_high:
            bos = True
        if direction == "short" and current_close < recent_low:
            bos = True

        # COS = rejet ou cassure inverse de structure faible
        if direction == "long" and current_low < recent_low and current_close < prev_close:
            cos = True
        if direction == "short" and current_high > recent_high and current_close > prev_close:
            cos = True

        # CHoCH = signal de retournement confirmé
        if direction == "long":
            choch = df["low"].iloc[-1] > df["low"].iloc[-5] and df["close"].iloc[-1] > df["close"].iloc[-5]
        else:
            choch = df["high"].iloc[-1] < df["high"].iloc[-5] and df["close"].iloc[-1] < df["close"].iloc[-5]

    except Exception as e:
        # En cas d'erreur, on logue éventuellement, mais on garde les trois à False
        pass

    return bos, cos, choch
