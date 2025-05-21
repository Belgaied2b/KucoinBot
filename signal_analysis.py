import pandas as pd

from indicators import (
    compute_rsi, compute_macd, compute_fvg,
    compute_ote, compute_atr, find_pivots
)
from risk_manager import calculate_rr
from scanner import is_cos_valid, is_bos_valid, is_btc_favorable


def analyze_signal(df: pd.DataFrame, direction: str = "long"):
    """
    Analyse complète d'un signal CONFIRMÉ pour swing avec pivots S/R :
    - FVG, OTE, BOS, COS, MA200, BTC validés
    - SL dynamique sur FVG + ATR + max 6% + pivots
    """
    try:
        # Calcul des indicateurs
        rsi = compute_rsi(df['close'])
        macd_line, signal_line = compute_macd(df['close'])
        fvg = compute_fvg(df, direction)
        ote = compute_ote(df, direction)

        price = df['close'].iat[-1]
        entry = ote['entry']
        sl = fvg['sl']
        atr = compute_atr(df).iat[-1]

        # Validation structurelle
        ma200 = df['close'].rolling(200).mean().iat[-1]
        ma_ok = (price > ma200) if direction == 'long' else (price < ma200)
        cos_ok = is_cos_valid(df)
        bos_ok = is_bos_valid(df)
        btc_ok = is_btc_favorable()

        print(f"[{df.name}] Check: FVG={fvg['valid']} | OTE={ote['in_ote']} | COS={cos_ok} | BOS={bos_ok} | MA200={ma_ok} | BTC={btc_ok}")
        if not all([fvg['valid'], ote['in_ote'], cos_ok, bos_ok, ma_ok, btc_ok]):
            print(f"[{df.name}] ❌ Rejeté (structure invalide)")
            return None

        # Recalcul SL si incohérent
        if (direction == 'long' and sl >= entry) or (direction == 'short' and sl <= entry):
            print(f"[{df.name}] ⚠️ SL incohérent. Recalcul automatique.")
            sl = entry - entry * 0.005 if direction == 'long' else entry + entry * 0.005

        # SL minimum = 1.5 × ATR
        min_dist = atr * 1.5
        if direction == 'long' and (entry - sl) < min_dist:
            sl = entry - min_dist
        elif direction == 'short' and (sl - entry) < min_dist:
            sl = entry + min_dist

        # SL maximum = 6 % de l'entry
        max_dist = entry * 0.06
        if direction == 'long' and (entry - sl) > max_dist:
            sl = entry - max_dist
        elif direction == 'short' and (sl - entry) > max_dist:
            sl = entry + max_dist

        # Détection de pivots
        highs, lows = find_pivots(df, window=5)
        if direction == 'long' and lows:
            pivot_price = df['low'].iat[lows[-1]]
            sl = min(sl, pivot_price - atr * 0.2)
        elif direction == 'short' and highs:
            pivot_price = df['high'].iat[highs[-1]]
            sl = max(sl, pivot_price + atr * 0.2)

        # Calcul TP selon RR souhaité (2.5)
        tp = calculate_rr(entry, sl, rr_ratio=2.5, direction=direction)

        # Calcul exact du R:R
        if direction == 'long':
            rr = (tp - entry) / (entry - sl)
        else:
            rr = (entry - tp) / (sl - entry)

        comment = f"🎯 Signal confirmé – entrée idéale après repli\n✔️ R:R = {rr:.2f}"
        print(f"[{df.name}] ✅ Signal CONFIRMÉ : Entry={entry:.8f}, SL={sl:.8f}, TP={tp:.8f}, R:R={rr:.2f}")

        return {
            'type': 'CONFIRMÉ',
            'direction': direction.upper(),
            'entry': round(entry, 8),
            'sl': round(sl, 8),
            'tp': round(tp, 8),
            'rsi': round(rsi.iat[-1], 2),
            'macd': round(macd_line.iat[-1], 6),
            'signal_line': round(signal_line.iat[-1], 6),
            'comment': comment,
            'ote_zone': ote.get('zone'),
            'fvg_zone': fvg.get('zone'),
            'symbol': df.name
        }
    except Exception as e:
        print(f"[{df.name}] ⚠️ Erreur dans analyze_signal : {e}")
        return None
