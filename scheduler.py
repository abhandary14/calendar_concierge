import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from agent import run_pipeline
from briefing import save_briefing
from logging_config import setup_logging

logger = logging.getLogger("concierge")


def daily_briefing() -> None:
    logger.info("Scheduled run starting")
    try:
        briefing = run_pipeline()
        path = save_briefing(briefing)
        logger.info("Scheduled run complete — status: %s, file: %s", briefing.status, path)
    except Exception:
        logger.exception("Scheduled run failed unexpectedly")


if __name__ == "__main__":
    setup_logging()
    scheduler = BlockingScheduler()
    scheduler.add_job(daily_briefing, "cron", hour=7, minute=30)
    print("Scheduler running — briefing fires daily at 07:30. Ctrl+C to stop.")
    scheduler.start()
