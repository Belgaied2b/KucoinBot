import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class TransientHTTPError(Exception): ...
class RateLimitError(Exception): ...

def backoff_retry(max_attempts=5, base=0.3, max_wait=3.0, exceptions=(Exception,)):
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=base, max=max_wait),
        retry=retry_if_exception_type(exceptions),
    )

def jitter_sleep(seconds: float):
    time.sleep(min(max(seconds, 0.0), 5.0))
