"""One digest run: fetch the window, summarize it, post it."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import httpx

from . import claude, config, discord_api, render, scheduler, transcript

log = logging.getLogger("summarizer.job")


async def run(start: datetime, end: datetime, *, dry_run: bool = False,
              show_transcript: bool = False,
              client: Optional[httpx.AsyncClient] = None) -> list[dict]:
    """Returns the payloads it posted (or would have, on a dry run)."""
    owned = client is None
    client = client or discord_api.new_client()
    channel_name = ""
    try:
        try:
            channel = await discord_api.fetch_channel(client)
            channel_name = channel.get("name") or ""
            payloads = await _build(channel, start, end, client, show_transcript)
        except Exception as exc:
            log.exception("Could not build the digest")
            payloads = [render.failure_payload(str(exc) or type(exc).__name__,
                                               channel_name)]
            if not dry_run:
                await _post(payloads, client,
                            config.ERROR_WEBHOOK_URL or config.DISCORD_WEBHOOK_URL)
            else:
                _print(payloads)
            return payloads

        if dry_run:
            _print(payloads)
        else:
            await _post(payloads, client, config.DISCORD_WEBHOOK_URL)
        return payloads
    finally:
        if owned:
            await client.aclose()


async def _build(channel: dict, start: datetime, end: datetime,
                 client: httpx.AsyncClient, show_transcript: bool) -> list[dict]:
    ctx = render.Context(
        guild_id=str(channel.get("guild_id") or "@me"),
        channel_id=str(channel.get("id") or config.DISCORD_CHANNEL_ID),
        channel_name=channel.get("name") or "channel",
        start=start.astimezone(scheduler.zone()),
        end=end.astimezone(scheduler.zone()),
    )
    messages = await discord_api.fetch_messages(start, end, client)
    if discord_api.content_intent_looks_disabled(messages):
        raise RuntimeError(
            f"all {len(messages)} messages came back empty. The Message Content "
            "intent is probably disabled for this bot - enable it under Bot > "
            "Privileged Gateway Intents in the Discord developer portal.")

    script = transcript.build(
        messages, scheduler.zone(),
        own_webhook_id=discord_api.webhook_id_from_url(config.DISCORD_WEBHOOK_URL),
        include_bots=config.INCLUDE_BOTS,
    )
    log.info("#%s %s: %d messages fetched, %d kept, %d people",
             ctx.channel_name, render.window_label(ctx), len(messages),
             len(script), script.people)
    if show_transcript:
        print(script.text)

    if len(script) < config.MIN_MESSAGES:
        return [render.quiet_payload(len(script), ctx)]

    header = (f"Channel: #{ctx.channel_name}\n"
              f"Window: {ctx.start:%a %b %d %Y %H:%M %Z} to {ctx.end:%a %b %d %Y %H:%M %Z}\n"
              f"Messages: {len(script)} from {script.people} people")
    summary = await claude.summarize(header, script)
    return render.digest_payloads(summary, script, ctx)


async def _post(payloads: list[dict], client: httpx.AsyncClient, url: str) -> None:
    for payload in payloads:
        await discord_api.post_webhook(url, payload, client)
    log.info("Posted %d message%s", len(payloads), "" if len(payloads) == 1 else "s")


def _print(payloads: list[dict]) -> None:
    print(json.dumps(payloads, indent=2, ensure_ascii=False))
