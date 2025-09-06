"""Self-test: verifies imports and basic wiring without hitting external networks.
Run: python self_test.py
"""
import os, importlib, sys, traceback

# Minimal env so config loads
os.environ.setdefault("KUCOIN_BASE_URL", "https://api-futures.kucoin.com")
os.environ.setdefault("KUCOIN_WS_URL", "wss://ws-api-futures.kucoin.com/endpoint")
os.environ.setdefault("KUCOIN_KEY", "demo")
os.environ.setdefault("KUCOIN_SECRET", "demo")
os.environ.setdefault("KUCOIN_PASSPHRASE", "demo")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "demo")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

mods = [
    "config","logger_utils","institutional_data","institutional_aggregator",
    "orderflow_features","strategy_setups","order_manager","kucoin_utils",
    "kucoin_trader","kucoin_ws","institutional_liq_ws","telegram_notifier",
    "scanner","analyze_signal","adverse_selection"
]

sys.path.insert(0, os.path.dirname(__file__))

ok = True
for m in mods:
    try:
        importlib.import_module(m)
        print(f"[OK] import {m}")
    except Exception as e:
        ok = False
        print(f"[FAIL] import {m}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=1)

if ok:
    print("\nSelf-test passed: all modules imported successfully.")
else:
    print("\nSelf-test FAILED. See errors above.")
