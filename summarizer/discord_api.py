"""Discord over plain REST: read a channel's history, post to a webhook.

No gateway connection and no discord.py. History is fetched at run time, which
is all a daily digest needs; the cost is that a message deleted before the run
is never seen.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from . import config

log = logging.getLogger("summarizer.discord")

DISCORD_EPOCH_MS = 1420070400000
MAX_RATE_LIMIT_RETRIES = 5

_WEBHOOK_RE = re.compile(r"/webhooks/(\d+)/")


class DiscordError(RuntimeError):
    pass


# -- Snowflakes --------------------------------------------------------------


def snowflake_for(instant: datetime) -> int:
    """A synthetic snowflake for a moment in time.

    Discord ids encode their creation time, so a fabricated one lets the window
    be applied server-side through ``after=`` instead of fetching history and
    filtering locally.
    """
    unix_ms = int(instant.timestamp() * 1000)
    return max(0, unix_ms - DISCORD_EPOCH_MS) << 22


def instant_for(snowflake: int | str) -> datetime:
    unix_ms = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc)


def webhook_id_from_url(url: str) -> Optional[str]:
    match = _WEBHOOK_RE.search(url or "")
    return match.group(1) if match else None


# -- Requests ----------------------------------------------------------------


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=config.DISCORD_TIMEOUT)


def _bot_headers() -> dict:
    return {"Authorization": f"Bot {config.DISCORD_BOT_TOKEN}"}


async def _request(client: httpx.AsyncClient, method: str, url: str, *,
                   what: str, **kwargs) -> httpx.Response:
    """One request, waiting out 429s.

    Errors name ``what`` rather than the URL: a webhook URL *is* its credential,
    and these messages end up in logs and in the failure note posted to Discord.
    """
    for _attempt in range(MAX_RATE_LIMIT_RETRIES):
        response = await client.request(method, url, **kwargs)
        if response.status_code == 429:
            retry = float(response.headers.get("Retry-After", "1") or 1)
            log.warning("Discord rate-limited %s; waiting %.1fs", what, retry)
            await asyncio.sleep(retry)
            continue
        if response.status_code >= 400:
            hint = ""
            if response.status_code in (401, 403):
                hint = (" - check the bot token, and that the bot has View Channel "
                        "and Read Message History on the channel")
            raise DiscordError(
                f"Discord returned {response.status_code} for {what}: "
                f"{response.text[:200]}{hint}"
            )
        return response
    raise DiscordError(f"Discord kept rate-limiting {what}")


async def fetch_channel(client: httpx.AsyncClient) -> dict:
    """The source channel, for its name and the guild id jump links need."""
    url = f"{config.DISCORD_API_BASE}/channels/{config.DISCORD_CHANNEL_ID}"
    response = await _request(client, "GET", url, what="the channel lookup",
                              headers=_bot_headers())
    return response.json()


async def fetch_messages(after: datetime, before: datetime,
                         client: httpx.AsyncClient) -> list[dict]:
    """Every message with ``after <= sent < before``, oldest first."""
    url = f"{config.DISCORD_API_BASE}/channels/{config.DISCORD_CHANNEL_ID}/messages"
    end = snowflake_for(before)
    # `after` is exclusive, so step back one to keep a message sent on the very
    # millisecond the window opens.
    cursor = max(0, snowflake_for(after) - 1)
    collected: dict[int, dict] = {}

    for _page in range(config.MAX_PAGES):
        response = await _request(
            client, "GET", url, what="the message history",
            params={"limit": 100, "after": cursor}, headers=_bot_headers(),
        )
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break
        for message in batch:
            collected[int(message["id"])] = message
        # Discord returns newest-first even with `after`, so page from the
        # highest id seen rather than the last element.
        cursor = max(int(m["id"]) for m in batch)
        if cursor >= end or len(batch) < 100:
            break
    else:
        log.warning("Stopped after MAX_PAGES=%d pages; the digest covers only the "
                    "first %d messages of the window", config.MAX_PAGES, len(collected))

    return [collected[i] for i in sorted(collected) if i < end]


def content_intent_looks_disabled(messages: list[dict]) -> bool:
    """Every message empty almost always means the Message Content intent is off.

    Silence and "nobody typed anything" look identical from here, and only one
    of them is a misconfiguration. Attachment-only messages are legitimately
    empty, so a handful proves nothing.
    """
    if len(messages) < 3:
        return False
    return all(not (m.get("content") or "").strip()
               and not m.get("attachments") and not m.get("embeds")
               for m in messages)


async def post_webhook(url: str, payload: dict, client: httpx.AsyncClient) -> dict:
    """POST one message. ``wait=true`` makes Discord confirm it was created
    instead of answering 204 to something it later drops."""
    response = await _request(client, "POST", url, what="the webhook post",
                              params={"wait": "true"}, json=payload)
    return response.json()
