"""Background worker entry-point for the catalog scraper.

Run locally (dev):
    python run_scheduler.py
    python run_scheduler.py --once          # run a single cycle then exit
    python run_scheduler.py --interval 2    # custom interval (hours)

On Render / Railway:
    Define a second service of type "worker" with start command:
        gunicorn wsgi:app   →   python run_scheduler.py
    or a Procfile "worker: python run_scheduler.py"
"""
import argparse
import logging
import os
import sys

# Make repo root importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("scheduler")


def main() -> int:
    parser = argparse.ArgumentParser(description="Background market-catalog worker")
    parser.add_argument("--once", action="store_true",
                        help="Run a single scrape cycle and exit")
    parser.add_argument("--interval", type=int,
                        default=int(os.environ.get("FPU_SCRAPER_INTERVAL_HOURS", 6)),
                        help="Hours between scrape cycles (default: 6)")
    args = parser.parse_args()

    if args.once:
        from services.scrapers import scrape_all_markets
        from app import create_app
        app = create_app()
        with app.app_context():
            results = scrape_all_markets()
        logger.info("Single cycle results: %s", results)
        return 0

    from services.scheduler import start_scheduler, stop_scheduler
    import signal
    import time

    sched = start_scheduler(interval_hours=args.interval)
    if sched is None:
        logger.error("Could not start scheduler (APScheduler missing). "
                     "Install: pip install apscheduler")
        return 1

    def _shutdown(signum, frame):
        logger.info("Received signal %s; shutting down", signum)
        stop_scheduler()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    logger.info("Worker running; Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())