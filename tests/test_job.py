import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from summarizer import claude, config, job

from .helpers import BASE, message

WEBHOOK = "https://discord.test/api/webhooks/777/main-token"
ERRORS = "https://discord.test/api/webhooks/888/error-token"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "555")
    monkeypatch.setattr(config, "DISCORD_API_BASE", "https://discord.test/api")
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setattr(config, "ERROR_WEBHOOK_URL", "")
    monkeypatch.setattr(config, "MIN_MESSAGES", 5)
    monkeypatch.setattr(config, "INCLUDE_BOTS", False)


class FakeDiscord:
    def __init__(self, messages, channel_status=200):
        self.messages = messages
        self.channel_status = channel_status
        self.posts = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/webhooks/" in path:
            self.posts.append((path, json.loads(request.content)))
            return httpx.Response(200, json={"id": "1"})
        if path.endswith("/messages"):
            after = int(request.url.params["after"])
            newer = [m for m in self.messages if int(m["id"]) > after][:100]
            return httpx.Response(200, json=list(reversed(newer)))
        if self.channel_status != 200:
            return httpx.Response(self.channel_status, json={"message": "Missing Access"})
        return httpx.Response(200, json={"id": "555", "guild_id": "111", "name": "general"})


def go(discord, **kwargs):
    async def inner():
        async with httpx.AsyncClient(transport=httpx.MockTransport(discord)) as client:
            return await job.run(BASE, BASE + timedelta(hours=24), client=client, **kwargs)
    return asyncio.run(inner())


def test_a_normal_day_posts_a_linked_digest(monkeypatch):
    chat = [message(i, "Alice" if i % 2 else "Bob", f"line {i}") for i in range(8)]
    seen = {}

    async def fake_summarize(header, script):
        seen["header"], seen["lines"] = header, len(script)
        return {"tldr": "Busy.", "quotes": [],
                "topics": [{"title": "Lines", "summary": "They counted.",
                            "participants": ["Alice"], "start_ref": "m3",
                            "key_messages": []}]}

    monkeypatch.setattr(claude, "summarize", fake_summarize)
    discord = FakeDiscord(chat)
    go(discord)

    assert seen["lines"] == 8
    assert "Channel: #general" in seen["header"] and "8 from 2 people" in seen["header"]
    [(path, payload)] = discord.posts
    assert path.endswith("/webhooks/777/main-token")
    assert (f"[Lines](https://discord.com/channels/111/555/{chat[2]['id']})"
            in payload["embeds"][0]["description"])


def test_a_quiet_day_posts_a_note_without_calling_claude(monkeypatch):
    async def never(*_):
        raise AssertionError("Claude must not be called on a quiet day")

    monkeypatch.setattr(claude, "summarize", never)
    discord = FakeDiscord([message(1, "Alice", "anyone here?")])
    go(discord)
    [(_, payload)] = discord.posts
    assert payload["content"].startswith("Quiet day in <#555> — 1 message ")


def test_yesterdays_digest_is_not_todays_input(monkeypatch):
    ours = message(0, "Digest", "x" * 50, webhook_id="777")
    ours["author"]["bot"] = True
    discord = FakeDiscord([ours] + [message(i, "Alice", "hi") for i in range(1, 4)])
    go(discord)
    # 3 human messages, below MIN_MESSAGES: the webhook's own post did not count.
    assert "3 messages" in discord.posts[0][1]["content"]


def test_dry_run_posts_nothing(monkeypatch, capsys):
    discord = FakeDiscord([])
    payloads = go(discord, dry_run=True)
    assert discord.posts == []
    assert json.loads(capsys.readouterr().out) == payloads


def test_claude_failure_is_reported_to_the_error_webhook(monkeypatch):
    async def broken(*_):
        raise claude.ClaudeError("claude reported an error: Invalid API key")

    monkeypatch.setattr(claude, "summarize", broken)
    monkeypatch.setattr(config, "ERROR_WEBHOOK_URL", ERRORS)
    discord = FakeDiscord([message(i, "Alice", "hi") for i in range(6)])
    go(discord)

    [(path, payload)] = discord.posts
    assert path.endswith("/webhooks/888/error-token")
    assert "Daily summary for #general failed" in payload["content"]
    assert "Invalid API key" in payload["content"]


def test_discord_failure_falls_back_to_the_main_webhook():
    discord = FakeDiscord([], channel_status=403)
    go(discord)
    [(path, payload)] = discord.posts
    assert path.endswith("/webhooks/777/main-token")
    assert "failed" in payload["content"] and "403" in payload["content"]


def test_disabled_message_content_intent_is_called_out(monkeypatch):
    discord = FakeDiscord([message(i, "Alice", "") for i in range(6)])
    go(discord)
    assert "Message Content intent" in discord.posts[0][1]["content"]
