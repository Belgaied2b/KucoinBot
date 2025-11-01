# metrics.py — hooks no-op si Prometheus absente
try:
    from prometheus_client import Counter, Histogram
    ORDER_OK = Counter("orders_ok_total","Orders success")
    ORDER_FAIL = Counter("orders_fail_total","Orders failed")
    FETCH_LAT = Histogram("fetch_seconds","HTTP fetch latencies",["endpoint"])
    def mark_order(ok:bool): (ORDER_OK if ok else ORDER_FAIL).inc()
    def mark_fetch(ep:str, dur:float): FETCH_LAT.labels(ep).observe(dur)
except Exception:
    def mark_order(ok:bool): pass
    def mark_fetch(ep:str, dur:float): pass
