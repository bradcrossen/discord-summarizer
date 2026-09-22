import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from summarizer import config, scheduler

PT = ZoneInfo("America/Los_Angeles")
CT = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SUMMARY_TIME", "20:00")
    monkeypatch.setattr(config, "SUMMARY_TZ", "America/Los_Angeles")
    monkeypatch.setattr(config, "CATCHUP_HOURS", 3.0)
    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")


def test_next_and_previous_run_around_the_hour():
    before = datetime(2026, 9, 21, 19, 59, tzinfo=PT)
    after = datetime(2026, 9, 21, 20, 0, tzinfo=PT)
    assert scheduler.next_run(before) == datetime(2026, 9, 21, 20, 0, tzinfo=PT)
    assert scheduler.next_run(after) == datetime(2026, 9, 22, 20, 0, tzinfo=PT)
    assert scheduler.previous_run(before) == datetime(2026, 9, 20, 20, 0, tzinfo=PT)
    assert scheduler.previous_run(after) == after


def test_now_may_arrive_in_any_timezone():
    # 21:30 Central is 19:30 Pacific: today's run is still ahead.
    now = datetime(2026, 9, 21, 21, 30, tzinfo=CT)
    assert scheduler.next_run(now) == datetime(2026, 9, 21, 20, 0, tzinfo=PT)
    utc = datetime(2026, 9, 22, 3, 30, tzinfo=timezone.utc)  # 20:30 PDT on the 21st
    assert scheduler.next_run(utc) == datetime(2026, 9, 22, 20, 0, tzinfo=PT)


@pytest.mark.parametrize("day", [
    datetime(2026, 3, 7), datetime(2026, 3, 8), datetime(2026, 3, 9),     # spring forward
    datetime(2026, 10, 31), datetime(2026, 11, 1), datetime(2026, 11, 2),  # fall back
    datetime(2026, 7, 4), datetime(2026, 12, 25),
])
def test_always_8pm_pacific_which_is_always_10pm_on_the_central_host(day):
    target = scheduler.next_run(datetime(day.year, day.month, day.day, 12, 0, tzinfo=PT))
    assert (target.hour, target.minute) == (20, 0)
    assert target.date() == day.date()
    host = target.astimezone(CT)
    assert (host.hour, host.minute) == (22, 0)


def test_windows_tile_the_calendar_across_dst():
    fall_back = datetime(2026, 11, 1, 20, 0, tzinfo=PT)
    assert scheduler._elapsed(*scheduler.window_for(fall_back)) == timedelta(hours=25)

    spring = datetime(2026, 3, 8, 20, 0, tzinfo=PT)
    start, end = scheduler.window_for(spring)
    assert scheduler._elapsed(start, end) == timedelta(hours=23)

    # Each window opens exactly where the previous one closed.
    next_start, _ = scheduler.window_for(datetime(2026, 3, 9, 20, 0, tzinfo=PT))
    assert next_start == end


def test_no_catchup_on_a_first_ever_start():
    assert scheduler.catchup_target(datetime(2026, 9, 21, 20, 30, tzinfo=PT)) is None


def test_catchup_after_downtime_but_not_twice_and_not_when_stale():
    scheduler.mark_completed(datetime(2026, 9, 20, 20, 0, tzinfo=PT))
    tonight = datetime(2026, 9, 21, 20, 0, tzinfo=PT)

    assert scheduler.catchup_target(tonight + timedelta(minutes=45)) == tonight
    assert scheduler.catchup_target(tonight + timedelta(hours=4)) is None

    scheduler.mark_completed(tonight)
    assert scheduler.catchup_target(tonight + timedelta(minutes=45)) is None


def test_corrupt_state_reads_as_no_state():
    config.STATE_PATH.write_text("{not json", encoding="utf-8")
    assert scheduler.last_completed() is None


def test_a_failed_run_is_still_marked_so_restarts_do_not_repost():
    end = datetime(2026, 9, 21, 20, 0, tzinfo=PT)
    seen = []

    async def job(start, stop):
        seen.append((start, stop))
        raise RuntimeError("boom")

    asyncio.run(scheduler._attempt(job, end))

    assert seen == [(datetime(2026, 9, 20, 20, 0, tzinfo=PT), end)]
    assert scheduler.last_completed() == end
