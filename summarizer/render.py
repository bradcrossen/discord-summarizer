"""Claude's digest + the transcript -> Discord webhook payloads.

Embeds rather than plain content: masked links render in an embed description
and the URLs inside one do not unfurl into previews.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from . import config
from .transcript import Line, Transcript

# Discord allows 4096 per description and 6000 per message across all embeds.
# One embed per message with headroom for title and footer stays under both.
MAX_DESCRIPTION = 4000
EMBED_COLOR = 0x5865F2

MAX_TLDR = 1000
MAX_TITLE = 100
MAX_SUMMARY = 1200
MAX_LABEL = 80
MAX_QUOTE = 280
MAX_PARTICIPANTS = 6
MAX_KEY_MESSAGES = 3
MAX_QUOTES = 4

# Set on every payload. A digest repeats what people typed, @everyone included,
# and must never be able to ping anybody.
NO_MENTIONS = {"parse": []}

# "{m12}" in Claude's text: the person who wrote m12. Braces, brackets, spacing
# and case are forgiven the same way refs are.
_PERSON = re.compile(r"\{\s*\[?\s*(m\d+)\s*\]?\s*\}", re.IGNORECASE)


@dataclass
class Context:
    guild_id: str
    channel_id: str
    channel_name: str
    start: datetime
    end: datetime


def jump_url(ctx: Context, line: Line) -> str:
    return f"https://discord.com/channels/{ctx.guild_id}/{ctx.channel_id}/{line.message_id}"


def _flat(text: object, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _link_text(text: object, limit: int) -> str:
    """Text that is safe inside the [...] of a masked link."""
    return _flat(text, limit).replace("[", "(").replace("]", ")")


def _named(text: object, transcript: Transcript) -> str:
    """Claude's text with each {m12} replaced by the name of whoever wrote m12.

    Claude never types the name of someone who spoke: it points at a message
    they wrote and the name comes from that message, so a name cannot be
    misremembered or pinned on the wrong person by spelling. A ref that is not
    in the transcript becomes "someone" rather than a guess.
    """
    def name(match: re.Match) -> str:
        line = transcript.get(match.group(1))
        return _flat(line.author, 40) if line else "someone"
    return _PERSON.sub(name, str(text or ""))


def _participants(refs: object, transcript: Transcript) -> list[str]:
    """Authors of the cited messages, in order, once each. A name Claude wrote
    instead of a ref is not a message anyone wrote, so it is dropped."""
    people: list[str] = []
    for ref in refs if isinstance(refs, list) else []:
        line = transcript.get(ref)
        if line and line.author not in people:
            people.append(line.author)
    return people[:MAX_PARTICIPANTS]


def _topic_block(topic: dict, transcript: Transcript, ctx: Context) -> str:
    title = _link_text(_named(topic.get("title"), transcript), MAX_TITLE) or "Untitled"
    start = transcript.get(topic.get("start_ref"))
    heading = f"**[{title}]({jump_url(ctx, start)})**" if start else f"**{title}**"
    rows = [heading, _flat(_named(topic.get("summary"), transcript), MAX_SUMMARY)]

    tail = []
    people = [_flat(p, 40) for p in _participants(topic.get("participants"), transcript)]
    if people:
        tail.append("*" + ", ".join(people) + "*")
    for key in (topic.get("key_messages") or [])[:MAX_KEY_MESSAGES]:
        # A ref that is not in the transcript is dropped, never guessed at.
        line = transcript.get(key.get("ref")) if isinstance(key, dict) else None
        if line:
            label = _link_text(_named(key.get("label"), transcript), MAX_LABEL) or "message"
            tail.append(f"[{label}]({jump_url(ctx, line)})")
    if tail:
        rows.append(" · ".join(tail))
    return "\n".join(rows)


def _quote_text(excerpt: object, line: Line) -> str:
    """Claude's excerpt only if the person really wrote it; otherwise their
    actual words. Whitespace and case are forgiven, nothing else is."""
    wanted = _flat(excerpt, MAX_QUOTE).strip("\"'“”‘’ ")
    actual = " ".join(line.text.split())
    if wanted and wanted.casefold() in actual.casefold():
        return wanted
    return _flat(actual, MAX_QUOTE)


def _quotes_block(quotes: list, transcript: Transcript, ctx: Context) -> str:
    rows = []
    seen = set()
    for quote in quotes:
        line = transcript.get(quote.get("ref"))
        # A forward's author only passed the words along, and would be credited
        # with them.
        if not line or line.ref in seen or line.forwarded:
            continue
        seen.add(line.ref)
        text = _quote_text(quote.get("excerpt"), line)
        if text:
            # Attribution comes from the message itself, not from Claude.
            rows.append(f"> “{text}” — **{_flat(line.author, 40)}** "
                        f"[↗]({jump_url(ctx, line)})")
        if len(rows) == MAX_QUOTES:
            break
    return "**Quotes**\n" + "\n".join(rows) if rows else ""


def _pack(blocks: list[str]) -> list[str]:
    """Blocks into descriptions, splitting only between blocks."""
    descriptions: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > MAX_DESCRIPTION:
            block = block[: MAX_DESCRIPTION - 1] + "…"
        joined = f"{current}\n\n{block}" if current else block
        if len(joined) > MAX_DESCRIPTION:
            descriptions.append(current)
            current = block
        else:
            current = joined
    if current:
        descriptions.append(current)
    return descriptions


def _day(moment: datetime) -> str:
    return f"{moment:%a, %b} {moment.day}"


def _clock(moment: datetime) -> str:
    return f"{moment:%b} {moment.day} {moment:%I:%M %p}".replace(" 0", " ")


def window_label(ctx: Context) -> str:
    return f"{_clock(ctx.start)} – {_clock(ctx.end)} {ctx.end:%Z}".strip()


def digest_payloads(summary: dict, transcript: Transcript, ctx: Context) -> list[dict]:
    blocks = [_flat(_named(summary.get("tldr"), transcript), MAX_TLDR)]
    blocks += [_topic_block(t, transcript, ctx) for t in summary.get("topics") or []]
    quotes = _quotes_block(summary.get("quotes") or [], transcript, ctx)
    if quotes:
        blocks.append(quotes)

    descriptions = _pack([b for b in blocks if b])
    payloads = []
    for index, description in enumerate(descriptions):
        embed = {"description": description, "color": EMBED_COLOR}
        if index == 0:
            embed["title"] = _flat(f"#{ctx.channel_name} — {_day(ctx.end)}", 256)
        if index == len(descriptions) - 1:
            people = transcript.people
            embed["footer"] = {"text": (
                f"{len(transcript)} messages · {people} "
                f"{'person' if people == 1 else 'people'} · {window_label(ctx)}")}
        payloads.append({"embeds": [embed], "allowed_mentions": NO_MENTIONS})
    return payloads


def quiet_payload(count: int, ctx: Context) -> dict:
    said = {0: "no messages", 1: "1 message"}.get(count, f"{count} messages")
    return {
        "content": f"Quiet day in <#{ctx.channel_id}> — {said} since "
                   f"{_clock(ctx.start)} {ctx.start:%Z}. Nothing to summarize.",
        "allowed_mentions": NO_MENTIONS,
    }


def redact(text: str) -> str:
    for secret in config.secrets():
        text = text.replace(secret, "[redacted]")
    return text


def failure_payload(reason: str, channel_name: str) -> dict:
    where = f"#{channel_name}" if channel_name else "the channel"
    return {
        "content": f"⚠️ Daily summary for {where} failed: {_flat(redact(reason), 400)}",
        "allowed_mentions": NO_MENTIONS,
    }
