import pandas as pd
from indicators import compute_rsi, compute_macd_histogram, compute_ma, compute_atr, compute_fvg_zones
from structure_utils import detect_bos_cos, detect_choch
from chart_generator import generate_chart

def analyze_signal(df, symbol, direction, context_macro):
    if df is None or df.empty or 'timestamp' not in df.columns:
        print(f"❌ Données manquantes pour {symbol}")
        return None

    df = df.copy()
    df.set_index("timestamp", inplace=True)
    df.index = pd.to_datetime(df.index)

    # Calculs indicateurs
    df["rsi"] = compute_rsi(df["close"])
    df["macd_histogram"] = compute_macd_histogram(df["close"])
    df["ma200"] = compute_ma(df)
    df["atr"] = compute_atr(df)
    df[["fvg_upper", "fvg_lower"]] = compute_fvg_zones(df)

    # Zone OTE (retracement Fibo)
    if direction == "long":
        fib_618 = df["low"].iloc[-2] + 0.618 * (df["high"].iloc[-2] - df["low"].iloc[-2])
        fib_786 = df["low"].iloc[-2] + 0.786 * (df["high"].iloc[-2] - df["low"].iloc[-2])
        ote_zone = (fib_618, fib_786)
        in_ote = fib_618 <= df["close"].iloc[-1] <= fib_786
    else:
        fib_127 = df["high"].iloc[-2] - 1.272 * (df["high"].iloc[-2] - df["low"].iloc[-2])
        fib_161 = df["high"].iloc[-2] - 1.618 * (df["high"].iloc[-2] - df["low"].iloc[-2])
        ote_zone = (fib_127, fib_161)
        in_ote = fib_161 <= df["close"].iloc[-1] <= fib_127

    # Zone FVG (directionnelle)
    latest_fvg = df.iloc[-1]
    if direction == "long":
        in_fvg = latest_fvg["fvg_lower"] is not None and df["close"].iloc[-1] > latest_fvg["fvg_lower"]
    else:
        in_fvg = latest_fvg["fvg_upper"] is not None and df["close"].iloc[-1] < latest_fvg["fvg_upper"]
    fvg_zone = (latest_fvg["fvg_lower"], latest_fvg["fvg_upper"])

    # MA200
    ma_ok = (df["close"].iloc[-1] > df["ma200"].iloc[-1]) if direction == "long" else (df["close"].iloc[-1] < df["ma200"].iloc[-1])

    # MACD
    macd_ok = df["macd_histogram"].iloc[-1] > 0 if direction == "long" else df["macd_histogram"].iloc[-1] < 0

    # RSI
    rsi = df["rsi"].iloc[-1]

    # ATR (volatilité)
    atr = df["atr"].iloc[-1]
    atr_ok = atr > df["atr"].mean()

    # Bougie de confirmation
    candle_size = abs(df["close"].iloc[-1] - df["open"].iloc[-1])
    full_size = df["high"].iloc[-1] - df["low"].iloc[-1]
    if full_size == 0:
        candle_ok = False
    else:
        candle_ok = (candle_size / full_size > 0.6) and df["volume"].iloc[-1] > df["volume"].rolling(20).mean().iloc[-1]

    # BOS / COS
    bos_ok, cos_ok = detect_bos_cos(df, direction)

    # CHoCH
    choch_ok = detect_choch(df, direction)

    # Macro context
    total_ok = context_macro["total_ok"]
    btc_d_trend = context_macro["btc_d_trend"]

    # DIVERGENCE (ex: RSI croissant, prix stagnant)
    rsi_trend = df["rsi"].iloc[-3:].diff().sum()
    price_trend = df["close"].iloc[-3:].diff().sum()
    divergence = (rsi_trend > 0 and price_trend <= 0) if direction == "long" else (rsi_trend < 0 and price_trend >= 0)

    # État des indicateurs
    conditions = {
        "OTE": in_ote,
        "FVG": in_fvg,
        "MA200": ma_ok,
        "MACD": macd_ok,
        "BOUGIE": candle_ok,
        "VOLUME": df["volume"].iloc[-1] > df["volume"].rolling(20).mean().iloc[-1],
        "BOS": bos_ok,
        "COS": cos_ok,
        "CHoCH": choch_ok,
        "ATR": atr_ok,
        "TOTAL": total_ok,
        "DIVERGENCE": divergence,
    }

    # Tolérance uniquement pour OTE, BOUGIE et DIVERGENCE
    tolerated = []
    rejected = []

    for key, valid in conditions.items():
        if not valid:
            if key in ["OTE", "BOUGIE", "DIVERGENCE"]:
                tolerated.append(key)
            else:
                rejected.append(key)

    if rejected:
        print(f"❌ {symbol} rejeté ({direction.upper()}) — Rejets : {rejected}")
        return None

    # Calcul des niveaux
    entry = df["close"].iloc[-1]
    sl_buffer = df["atr"].iloc[-1]

    if direction == "long":
        sl = df["low"].iloc[-2] - sl_buffer
        tp = entry + (entry - sl) * 2
    else:
        sl = df["high"].iloc[-2] + sl_buffer
        tp = entry - (sl - entry) * 2

    # Génération du graphique
    generate_chart(df, symbol, ote_zone, fvg_zone, entry, sl, tp, direction)

    # Message Telegram clair
    comment = f"""
📊 Signal CONFIRMÉ — {symbol} — {direction.upper()}

🎯 Entrée : `{entry:.4f}`
🛡️ Stop Loss : `{sl:.4f}`
🎯 Take Profit : `{tp:.4f}`

⚠️ Zone idéale : {"✅" if in_ote and in_fvg else "NON atteinte"}  
➤ OTE : {ote_zone}  
➤ FVG : {fvg_zone}  
➤ RSI : `{rsi:.1f}`
➤ BTC.D : `{btc_d_trend.upper()}`

❌ Rejetés : {', '.join(rejected) if rejected else "Aucun"}
⚠️ Tolérés : {', '.join(tolerated) if tolerated else "Aucun"}
    """.strip()

    print(f"✅ {symbol} validé ({direction.upper()}) | Score ≈ {12 - len(rejected) - 0.5 * len(tolerated)}/12")

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "comment": comment
    }
