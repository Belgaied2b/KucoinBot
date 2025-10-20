"""
main.py
Bot Telegram qui scanne automatiquement toutes les 5 minutes.
"""
from apscheduler.schedulers.blocking import BlockingScheduler
from scanner import scan_and_send_signals

def job():
    print("🚀 Scan en cours...")
    scan_and_send_signals()

if __name__ == "__main__":
    sched = BlockingScheduler()
    sched.add_job(job, 'interval', minutes=5)
    job()
    sched.start()
