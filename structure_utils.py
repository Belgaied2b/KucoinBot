def detect_bos(df, direction):
    try:
        if len(df) < 30:
            return False

        if direction == "long":
            recent_high = df['high'].iloc[-2]
            previous_highs = df['high'].iloc[-20:-2]
            return recent_high > previous_highs.max()
        else:
            recent_low = df['low'].iloc[-2]
            previous_lows = df['low'].iloc[-20:-2]
            return recent_low < previous_lows.min()
    except Exception as e:
        print(f"[BOS] Erreur: {e}")
        return False

def detect_cos(df, direction):
    try:
        if len(df) < 30:
            return False

        if direction == "long":
            recent_low = df['low'].iloc[-2]
            previous_lows = df['low'].iloc[-20:-2]
            return recent_low > previous_lows.max()
        else:
            recent_high = df['high'].iloc[-2]
            previous_highs = df['high'].iloc[-20:-2]
            return recent_high < previous_highs.min()
    except Exception as e:
        print(f"[COS] Erreur: {e}")
        return False

def detect_choch(df, direction):
    try:
        if len(df) < 30:
            return False

        if direction == "long":
            # Un plus bas cassé puis un plus haut franchi
            low_broken = df['low'].iloc[-5:-2].min() < df['low'].iloc[-20:-5].min()
            high_made = df['high'].iloc[-2] > df['high'].iloc[-20:-2].max()
            return low_broken and high_made
        else:
            # Un plus haut cassé puis un plus bas franchi
            high_broken = df['high'].iloc[-5:-2].max() > df['high'].iloc[-20:-5].max()
            low_made = df['low'].iloc[-2] < df['low'].iloc[-20:-2].min()
            return high_broken and low_made
    except Exception as e:
        print(f"[CHoCH] Erreur: {e}")
        return False
