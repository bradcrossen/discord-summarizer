from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from summarizer import config
from summarizer.__main__ import manual_window

PT = ZoneInfo("America/Los_Angeles")


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_TIME", "20:00")
    monkeypatch.setattr(config, "SUMMARY_TZ", "America/Los_Angeles")


@pytest.mark.parametrize("now", [
    datetime(2026, 9, 25, 20, 18, tzinfo=PT),               # just after the run
    datetime(2026, 9, 26, 3, 18, tzinfo=timezone.utc),      # the same moment in UTC
    datetime(2026, 9, 26, 19, 59, tzinfo=PT),               # right before the next one
])
def test_redo_is_the_last_scheduled_window_whenever_it_is_run(now):
    assert manual_window(now, None, redo=True) == (
        datetime(2026, 9, 24, 20, 0, tzinfo=PT), datetime(2026, 9, 25, 20, 0, tzinfo=PT))


def test_once_and_hours_still_count_back_from_now():
    now = datetime(2026, 9, 25, 20, 18, tzinfo=PT)
    assert manual_window(now, None, redo=False) == (now - timedelta(hours=24), now)
    assert manual_window(now, 6.0, redo=False) == (now - timedelta(hours=6), now)
