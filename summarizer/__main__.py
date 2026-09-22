"""python -m summarizer            run the daily schedule (the container's CMD)
python -m summarizer --once     digest the last 24 hours now
python -m summarizer --hours 6  digest the last 6 hours now
        --dry-run               print the webhook payload instead of posting it
        --show-transcript       also print what Claude is given
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from . import config, job, scheduler


def main() -> int:
    parser = argparse.ArgumentParser(prog="summarizer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--hours", type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-transcript", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # httpx logs every request URL at INFO, and a webhook URL is its own
    # credential. Keep it out of `docker logs`.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    manual = args.once or args.hours is not None or args.dry_run
    unset = config.missing(dry_run=args.dry_run)
    if unset:
        logging.error("Not configured: set %s. See the README.", ", ".join(unset))
        return 2

    log = logging.getLogger("summarizer")
    log.info("discord-summarizer commit=%s model=%s schedule=%s %s",
             config.GIT_SHA, config.SUMMARY_MODEL, config.SUMMARY_TIME, config.SUMMARY_TZ)

    if manual:
        # A manual run never touches the schedule's state, so trying the bot out
        # at 7PM cannot suppress the real digest at 8.
        # UTC, so "24 hours" is elapsed time even across a DST change.
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=args.hours if args.hours is not None else 24)
        asyncio.run(job.run(start, end, dry_run=args.dry_run,
                            show_transcript=args.show_transcript))
        return 0

    # `docker stop` sends SIGTERM, which PID 1 ignores unless it has a handler:
    # without this every stop waits out the 10s grace period and is then killed.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        asyncio.run(scheduler.run_forever(job.run))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
