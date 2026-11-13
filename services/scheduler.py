"""APScheduler integration for background catalog scraping.

The scheduler runs as a **separate process** so it never slows down the
request-serving web app:

    python run_scheduler.py            # foreground (dev)
    python run_scheduler.py --daemon   # detach on POSIX

On Render / Railway (stateless containers), run the scheduler as a *second*
worker service (e.g. an extra "worker" service in render.yaml) so the web
service stays stateless.  In dev, start both processes side-by-side.

The default schedule is every 6 hours; override via FPU_SCRAPER_INTERVAL_HOURS.
"""
import logging
import os
from typing import Optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    _APSC = True
except Exception:  # pragma: no cover - optional dependency
    BackgroundScheduler = None
    IntervalTrigger = None
    _APSC = False

from services.scrapers import scrape_all_markets

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _job():
    """Run one scrape cycle inside its own app context."""
    from app import create_app
    try:
        app = create_app()
        with app.app_context():
            results = scrape_all_markets()
        logger.info("Scrape cycle complete: %s", results)
    except Exception:
        logger.exception("Scrape cycle failed")


def start_scheduler(interval_hours: int = 6) -> Optional[BackgroundScheduler]:
    """Start the background scheduler (idempotent).

    Returns the scheduler instance, or None when APScheduler is not
    installed.  Call this from the worker entry-point, NOT from the
    request-serving app.
    """
    global _scheduler
    if not _APSC:
        logger.warning("APScheduler not installed; background scraping disabled. "
                       "Install with: pip install apscheduler")
        return None
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _job,
        trigger=IntervalTrigger(hours=interval_hours),
        id="scrape_markets",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Scheduler started; next run in %d hours", interval_hours)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None


def is_running() -> bool:
    return bool(_scheduler and _scheduler.running)