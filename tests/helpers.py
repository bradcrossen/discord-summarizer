"""Builders for fake Discord messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from summarizer.discord_api import snowflake_for

BASE = datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc)


def snowflake(minutes: float) -> str:
    """An id for a message sent ``minutes`` after BASE. The +1 keeps it distinct
    from the synthetic window-edge snowflake for the same instant."""
    return str(snowflake_for(BASE + timedelta(minutes=minutes)) + 1)


def message(minutes: float, author: str, content: str, *, author_id: str | None = None,
            **extra) -> dict:
    return {
        "id": snowflake(minutes),
        "type": 0,
        "content": content,
        "author": {"id": author_id or f"id-{author}", "username": author.lower(),
                   "global_name": author},
        "mentions": [],
        "attachments": [],
        "embeds": [],
        **extra,
    }
