"""When to run, and remembering that we did.

The schedule is wall-clock time in SUMMARY_TZ, independent of the container's
own TZ: the Unraid host is on Central and the digest is due at 8PM Pacific.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from . import config

log = logging.getLogger("summarizer.scheduler")


def zone() -> ZoneInfo:
    return ZoneInfo(config.SUMMARY_TZ)


def run_time() -> time:
    hour, _, minute = config.SUMMARY_TIME.partition(":")
    return time(int(hour), int(minute or 0))


def scheduled_at(day: date) -> datetime:
    # A ZoneInfo resolves the UTC offset for that date's wall time, so the run
    # stays at 20:00 local across both DST changes.
    return datetime.combine(day, run_time(), tzinfo=zone())


def previous_run(now: datetime) -> datetime:
    """The latest scheduled instant at or before ``now``."""
    local = now.astimezone(zone())
    today = scheduled_at(local.date())
    return today if today <= local else scheduled_at(local.date() - timedelta(days=1))


def next_run(now: datetime) -> datetime:
    """The earliest scheduled instant after ``now``."""
    local = now.astimezone(zone())
    today = scheduled_at(local.date())
    return today if today > local else scheduled_at(local.date() + timedelta(days=1))


def window_for(end: datetime) -> tuple[datetime, datetime]:
    """Previous scheduled run -> this one. Consecutive windows share an edge, so
    nothing is dropped or counted twice; the span is 23h or 25h on a DST day."""
    local = end.astimezone(zone())
    return scheduled_at(local.date() - timedelta(days=1)), end


def _elapsed(earlier: datetime, later: datetime) -> timedelta:
    """Real elapsed time. Subtracting two datetimes that share a tzinfo compares
    their wall clocks and ignores the offset, which is an hour out across a DST
    change - so do the arithmetic in UTC."""
    return later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)


# -- State -------------------------------------------------------------------


def last_completed() -> Optional[datetime]:
    try:
        raw = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        return datetime.fromisoformat(raw["last_window_end"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def mark_completed(end: datetime) -> None:
    config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.STATE_PATH.write_text(
        json.dumps({"last_window_end": end.isoformat()}), encoding="utf-8")


def catchup_target(now: datetime) -> Optional[datetime]:
    """A run that was missed while the container was down, if still worth posting.

    A first-ever start has no state and posts nothing: `--once` is the way to
    try the bot out, and a surprise digest on install is not.
    """
    done = last_completed()
    if done is None:
        return None
    missed = previous_run(now)
    late = _elapsed(missed, now)
    if _elapsed(done, missed) > timedelta(0) and late <= timedelta(hours=config.CATCHUP_HOURS):
        return missed
    return None


# -- Loop --------------------------------------------------------------------


async def _sleep_until(target: datetime) -> None:
    # Short naps, re-reading the clock each time: one long sleep drifts if the
    # host suspends or NTP steps the clock.
    while True:
        remaining = _elapsed(datetime.now(timezone.utc), target).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 300))


async def run_forever(job: Callable[[datetime, datetime], Awaitable[None]]) -> None:
    now = datetime.now(zone())
    missed = catchup_target(now)
    if missed:
        log.info("Missed the %s run while down; catching up now", missed.isoformat())
        await _attempt(job, missed)

    while True:
        target = next_run(datetime.now(zone()))
        log.info("Next digest at %s (%s host time)",
                 f"{target:%Y-%m-%d %H:%M %Z}",
                 f"{target.astimezone():%Y-%m-%d %H:%M %Z}")
        await _sleep_until(target)
        await _attempt(job, target)


async def _attempt(job: Callable[[datetime, datetime], Awaitable[None]],
                   end: datetime) -> None:
    start, end = window_for(end)
    try:
        await job(start, end)
    except Exception:
        # The job reports its own failures to Discord. Nothing may kill the loop.
        log.exception("Digest run failed")
    # Marked even after a failure: a crash-looping container must not post a
    # failure note on every restart for the rest of the catch-up window.
    mark_completed(end)
