def detect_bos(df, direction):
    try:
        if len(df) < 30:
            return False

        current_close = df['close'].iloc[-1]
        current_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]

        if direction == "long":
            recent_high = df['high'].iloc[-2]
            previous_highs = df['high'].iloc[-20:-2]
            high_breakout = recent_high > previous_highs.max()
            candle_confirm = current_close > previous_highs.max()
            volume_confirm = current_volume > avg_volume * 1.2
            return high_breakout and candle_confirm and volume_confirm

        else:
            recent_low = df['low'].iloc[-2]
            previous_lows = df['low'].iloc[-20:-2]
            low_breakout = recent_low < previous_lows.min()
            candle_confirm = current_close < previous_lows.min()
            volume_confirm = current_volume > avg_volume * 1.2
            return low_breakout and candle_confirm and volume_confirm

    except Exception as e:
        print(f"[BOS] Erreur: {e}")
        return False


def detect_cos(df, direction):
    try:
        if len(df) < 30:
            return False

        current_close = df['close'].iloc[-1]
        current_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]

        if direction == "long":
            recent_low = df['low'].iloc[-2]
            previous_lows = df['low'].iloc[-20:-2]
            low_breakout = recent_low > previous_lows.max()
            candle_confirm = current_close > previous_lows.max()
            volume_confirm = current_volume > avg_volume * 1.2
            return low_breakout and candle_confirm and volume_confirm

        else:
            recent_high = df['high'].iloc[-2]
            previous_highs = df['high'].iloc[-20:-2]
            high_breakout = recent_high < previous_highs.min()
            candle_confirm = current_close < previous_highs.min()
            volume_confirm = current_volume > avg_volume * 1.2
            return high_breakout and candle_confirm and volume_confirm

    except Exception as e:
        print(f"[COS] Erreur: {e}")
        return False


def detect_choch(df, direction):
    try:
        if len(df) < 30:
            return False

        current_close = df['close'].iloc[-1]
        current_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]

        if direction == "long":
            low_broken = df['low'].iloc[-5:-2].min() < df['low'].iloc[-20:-5].min()
            high_made = df['high'].iloc[-2] > df['high'].iloc[-20:-2].max()
            high_breakout_level = df['high'].iloc[-20:-2].max()
            candle_confirm = current_close > high_breakout_level
            volume_confirm = current_volume > avg_volume * 1.2
            return low_broken and high_made and candle_confirm and volume_confirm

        else:
            high_broken = df['high'].iloc[-5:-2].max() > df['high'].iloc[-20:-5].max()
            low_made = df['low'].iloc[-2] < df['low'].iloc[-20:-2].min()
            low_breakout_level = df['low'].iloc[-20:-2].min()
            candle_confirm = current_close < low_breakout_level
            volume_confirm = current_volume > avg_volume * 1.2
            return high_broken and low_made and candle_confirm and volume_confirm

    except Exception as e:
        print(f"[CHoCH] Erreur: {e}")
        return False
