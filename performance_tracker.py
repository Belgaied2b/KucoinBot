import pandas as pd
from datetime import datetime

SIGNALS_CSV = "signals_history.csv"

def update_signal_status(symbol, direction, current_price):
    try:
        df = pd.read_csv(SIGNALS_CSV)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        mask = (df["symbol"] == symbol) & (df["direction"] == direction.upper()) & (df["status"].isnull())

        for idx in df[mask].index:
            entry = df.at[idx, "entry"]
            sl = df.at[idx, "sl"]
            tp = df.at[idx, "tp"]

            if direction == "long":
                if current_price >= tp:
                    df.at[idx, "status"] = "TP"
                    df.at[idx, "closed_at"] = datetime.utcnow()
                elif current_price <= sl:
                    df.at[idx, "status"] = "SL"
                    df.at[idx, "closed_at"] = datetime.utcnow()
            else:
                if current_price <= tp:
                    df.at[idx, "status"] = "TP"
                    df.at[idx, "closed_at"] = datetime.utcnow()
                elif current_price >= sl:
                    df.at[idx, "status"] = "SL"
                    df.at[idx, "closed_at"] = datetime.utcnow()

        df.to_csv(SIGNALS_CSV, index=False)
    except Exception as e:
        print(f"Erreur lors de la mise à jour des signaux : {e}")

def compute_statistics():
    try:
        df = pd.read_csv(SIGNALS_CSV)
        total = len(df)
        wins = len(df[df["status"] == "TP"])
        losses = len(df[df["status"] == "SL"])
        open_trades = len(df[df["status"].isnull()])
        winrate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0

        return {
            "total_signals": total,
            "TP": wins,
            "SL": losses,
            "open": open_trades,
            "winrate": round(winrate, 2)
        }
    except:
        return {
            "total_signals": 0,
            "TP": 0,
            "SL": 0,
            "open": 0,
            "winrate": 0.0
        }
