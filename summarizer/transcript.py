"""Discord messages -> the numbered transcript Claude reads.

Every kept message gets a short ref (``m12``). Claude only ever answers in refs;
render.py turns them back into jump links and real quote text, so neither a URL
nor an attribution can be invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Optional

from .discord_api import instant_for

# DEFAULT and REPLY. Everything else is a system line (joins, pins, boosts,
# thread-created notices) that would only be noise in a digest.
KEPT_TYPES = {0, 19}

# One message cannot crowd out the day. The full text is still kept for quotes.
MAX_LINE_CHARS = 1500

_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&\d+>")
_CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")
_TRANSCRIPT_TAG = re.compile(r"<(/?)transcript", re.IGNORECASE)


@dataclass
class Line:
    ref: str
    message_id: str
    author: str
    author_id: str
    sent: datetime
    text: str
    rendered: str


@dataclass
class Transcript:
    lines: list[Line] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.by_ref = {line.ref: line for line in self.lines}

    def __len__(self) -> int:
        return len(self.lines)

    @property
    def people(self) -> int:
        return len({line.author_id for line in self.lines})

    @property
    def text(self) -> str:
        return "\n".join(line.rendered for line in self.lines)

    def get(self, ref: object) -> Optional[Line]:
        """Tolerant lookup: Claude is told to write ``m12`` but ``[m12]`` is an
        easy slip, and a dropped link is a worse outcome than a forgiven one."""
        if not isinstance(ref, str):
            return None
        return self.by_ref.get(ref.strip().strip("[]").lower())

    def chunks(self, max_chars: int) -> list[str]:
        """The transcript in order, split on line boundaries."""
        parts: list[str] = []
        current: list[str] = []
        size = 0
        for line in self.lines:
            if current and size + len(line.rendered) + 1 > max_chars:
                parts.append("\n".join(current))
                current, size = [], 0
            current.append(line.rendered)
            size += len(line.rendered) + 1
        if current:
            parts.append("\n".join(current))
        return parts


def display_name(user: dict) -> str:
    name = user.get("global_name") or user.get("username") or "unknown"
    return " ".join(str(name).split()) or "unknown"


def clean_content(message: dict) -> str:
    """Raw markup -> what a person saw: @names instead of <@ids>."""
    names = {str(u.get("id")): display_name(u) for u in message.get("mentions") or []}
    text = message.get("content") or ""
    text = _USER_MENTION.sub(lambda m: "@" + names.get(m.group(1), "someone"), text)
    text = _ROLE_MENTION.sub("@role", text)
    text = _CUSTOM_EMOJI.sub(lambda m: f":{m.group(1)}:", text)
    # The transcript is delimited by <transcript> tags in the prompt. A message
    # must not be able to close that block and speak as the prompt.
    text = _TRANSCRIPT_TAG.sub(lambda m: f"<​{m.group(1)}transcript", text)
    return text.strip()


def _extras(message: dict) -> list[str]:
    notes = [f"[attachment: {a.get('filename', 'file')}]"
             for a in message.get("attachments") or []]
    for embed in message.get("embeds") or []:
        label = embed.get("title") or embed.get("url")
        if label:
            notes.append(f"[link: {' '.join(str(label).split())[:120]}]")
    notes += [f"[sticker: {s.get('name', 'sticker')}]"
              for s in message.get("sticker_items") or []]
    return notes


def _keep(message: dict, own_webhook_id: Optional[str], include_bots: bool) -> bool:
    if message.get("type", 0) not in KEPT_TYPES:
        return False
    # Our own digests, when the webhook posts into the channel it summarizes.
    # Checked before include_bots so that switch can never feed them back in.
    if own_webhook_id and str(message.get("webhook_id") or "") == own_webhook_id:
        return False
    if (message.get("author") or {}).get("bot") and not include_bots:
        return False
    return True


def build(messages: list[dict], tz: tzinfo, own_webhook_id: Optional[str] = None,
          include_bots: bool = False) -> Transcript:
    """``messages`` oldest first, as discord_api.fetch_messages returns them."""
    lines: list[Line] = []
    ref_by_id: dict[str, str] = {}

    for message in messages:
        if not _keep(message, own_webhook_id, include_bots):
            continue
        text = clean_content(message)
        if not text:
            # A forward carries its text in a snapshot, not in `content`.
            for snap in message.get("message_snapshots") or []:
                inner = clean_content(snap.get("message") or {})
                if inner:
                    text = f"[forwarded] {inner}"
                    break
        extras = _extras(message)
        if not text and not extras:
            continue

        ref = f"m{len(lines) + 1}"
        message_id = str(message["id"])
        ref_by_id[message_id] = ref
        author = message.get("author") or {}
        sent = instant_for(message_id).astimezone(tz)

        head = f"[{ref}] {sent:%H:%M} {display_name(author)}"
        reply_to = (message.get("message_reference") or {}).get("message_id")
        if message.get("type") == 19 and reply_to:
            head += f" (↩ {ref_by_id.get(str(reply_to), 'an earlier message')})"
        reactions = sum(int(r.get("count") or 0) for r in message.get("reactions") or [])
        if reactions:
            head += f" [{reactions} reaction{'s' if reactions != 1 else ''}]"

        body = " ⏎ ".join(part.strip() for part in text.splitlines() if part.strip())
        if len(body) > MAX_LINE_CHARS:
            body = body[:MAX_LINE_CHARS] + "…"
        rendered = f"{head}: {' '.join([body, *extras]).strip()}"

        lines.append(Line(ref=ref, message_id=message_id, author=display_name(author),
                          author_id=str(author.get("id") or display_name(author)),
                          sent=sent, text=text, rendered=rendered))

    return Transcript(lines)
