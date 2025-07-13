import os
import csv
import pandas as pd

def calculate_ote_zone(df, direction="long"):
    high = df["high"].iloc[-30:].max()
    low = df["low"].iloc[-30:].min()
    if direction == "long":
        fib_618 = low + 0.618 * (high - low)
        fib_786 = low + 0.786 * (high - low)
        return (fib_618, fib_786)
    else:
        fib_1272 = high - 0.272 * (high - low)
        fib_1618 = high - 0.618 * (high - low)
        return (fib_1272, fib_1618)

def find_entry_in_ote_fvg(df, ote_zone, fvg_zones, direction="long"):
    price = df["close"].iloc[-1]
    in_ote = ote_zone[0] <= price <= ote_zone[1]
    in_fvg = any(fvg[0] <= price <= fvg[1] for fvg in fvg_zones)
    if in_ote and in_fvg:
        return price
    return None

def find_dynamic_tp(df, entry, sl, direction="long", min_rr=1.5):
    if direction == "long":
        potential_tps = df["high"].iloc[-30:][df["high"].iloc[-30:] > entry].sort_values()
    else:
        potential_tps = df["low"].iloc[-30:][df["low"].iloc[-30:] < entry].sort_values(ascending=False)

    for level in potential_tps:
        rr = abs(level - entry) / abs(entry - sl)
        if rr >= min_rr:
            return level
    return None

def save_signal_to_csv(result, filename="signals_history.csv"):
    file_exists = os.path.isfile(filename)
    with open(filename, "a", newline="") as csvfile:
        fieldnames = ["timestamp", "symbol", "direction", "entry", "sl", "tp", "score", "comment"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": pd.Timestamp.now(),
            "symbol": result["symbol"],
            "direction": result["direction"],
            "entry": result["entry"],
            "sl": result["sl"],
            "tp": result["tp"],
            "score": result["score"],
            "comment": result["comment"]
        })

def log_message(symbol, direction, result):
    print(f"📡 {symbol} [{direction.upper()}] — Score: {result['score']}/10")
    if result["rejetes"]:
        print(f"❌ Rejetés : {', '.join(result['rejetes'])}")
    if result["toleres"]:
        print(f"⚠️ Tolérés : {', '.join(result['toleres'])}")
    print("—" * 40)
