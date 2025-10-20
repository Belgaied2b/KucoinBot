def to_binance(sym: str) -> str:
    return sym.upper().replace("USDTM","USDT").replace("USDCM","USDC")

def to_okx(sym: str) -> str:
    # OKX uses /: e.g., BTC-USDT-SWAP
    s = sym.upper().replace("USDTM","USDT")
    base = s.replace("USDT","")
    return f"{base}-USDT-SWAP"

def to_bybit(sym: str) -> str:
    return sym.upper().replace("USDTM","USDT")
