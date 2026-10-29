"""Run all scheduled jobs once (for cron on Hostinger / multi-instance deployments).

    python -m app.scheduler.run_once
"""

import app.models  # noqa: F401
from app.scheduler.jobs import run_daily_jobs, run_frequent_jobs, send_namaz_alerts

if __name__ == "__main__":
    print("frequent:", run_frequent_jobs())
    print("daily:", run_daily_jobs())
    print("namaz alerts:", send_namaz_alerts())
