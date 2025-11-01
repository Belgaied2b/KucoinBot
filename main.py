import os
from apscheduler.schedulers.blocking import BlockingScheduler
from logger import configure_logging
from scanner import scan_and_send_signals
from settings import SCAN_INTERVAL_MIN, TZ

def job():
    print("🚀 Scan en cours...")
    scan_and_send_signals()

if __name__ == "__main__":
    os.environ["TZ"] = TZ
    configure_logging()
    sched = BlockingScheduler(timezone=TZ)
    # premier run immédiat + planification
    job()
    sched.add_job(job, 'interval', minutes=SCAN_INTERVAL_MIN, id="scanner-job", replace_existing=True)
    sched.start()
