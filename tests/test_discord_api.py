import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from summarizer import config, discord_api

from .helpers import BASE, message


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "555")
    monkeypatch.setattr(config, "DISCORD_API_BASE", "https://discord.test/api")


def run(handler, coro_factory):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await coro_factory(client)
    return asyncio.run(go())


def test_snowflake_round_trips_to_the_millisecond():
    assert discord_api.instant_for(discord_api.snowflake_for(BASE)) == BASE


def test_webhook_id_from_url():
    url = "https://discord.com/api/webhooks/123456/abc-DEF_token"
    assert discord_api.webhook_id_from_url(url) == "123456"
    assert discord_api.webhook_id_from_url("") is None


def test_fetch_pages_from_the_highest_id_and_trims_the_window():
    # 250 messages a minute apart; the window closes after the 230th.
    everything = [message(i, "Alice", f"msg {i}") for i in range(250)]
    before = BASE + timedelta(minutes=229, seconds=30)
    cursors = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bot bot-token"
        after = int(request.url.params["after"])
        cursors.append(after)
        newer = [m for m in everything if int(m["id"]) > after][:100]
        # Discord answers newest-first even when paging forward with `after`.
        return httpx.Response(200, json=list(reversed(newer)))

    got = run(handler, lambda c: discord_api.fetch_messages(BASE, before, c))

    assert [m["content"] for m in got] == [f"msg {i}" for i in range(230)]
    assert cursors[1] == int(everything[99]["id"])
    assert len(cursors) == 3


def test_fetch_waits_out_a_rate_limit(monkeypatch):
    naps = []

    async def fake_sleep(seconds):
        naps.append(seconds)

    monkeypatch.setattr(discord_api.asyncio, "sleep", fake_sleep)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.5"}, json={})
        return httpx.Response(200, json=[message(1, "Alice", "hi")])

    got = run(handler, lambda c: discord_api.fetch_messages(
        BASE, BASE + timedelta(hours=1), c))
    assert len(got) == 1 and naps == [0.5]


def test_errors_never_carry_the_url():
    secret_url = "https://discord.test/api/webhooks/1/super-secret-token"

    def handler(request):
        return httpx.Response(404, json={"message": "Unknown Webhook"})

    with pytest.raises(discord_api.DiscordError) as caught:
        run(handler, lambda c: discord_api.post_webhook(secret_url, {}, c))
    assert "super-secret-token" not in str(caught.value)
    assert "404" in str(caught.value)


def test_forbidden_says_what_to_check():
    def handler(request):
        return httpx.Response(403, json={"message": "Missing Access"})

    with pytest.raises(discord_api.DiscordError, match="Read Message History"):
        run(handler, discord_api.fetch_channel)


def test_post_webhook_waits_for_confirmation():
    seen = {}

    def handler(request):
        seen["wait"] = request.url.params.get("wait")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "1"})

    run(handler, lambda c: discord_api.post_webhook(
        "https://discord.test/api/webhooks/1/tok", {"content": "hi"}, c))
    assert seen == {"wait": "true", "body": {"content": "hi"}}


def test_content_intent_check():
    empty = [message(i, "Alice", "") for i in range(5)]
    assert discord_api.content_intent_looks_disabled(empty)
    assert not discord_api.content_intent_looks_disabled(empty[:2])
    assert not discord_api.content_intent_looks_disabled(
        empty + [message(9, "Bob", "hello")])
    pictures = [message(i, "Alice", "", attachments=[{"filename": "a.png"}])
                for i in range(5)]
    assert not discord_api.content_intent_looks_disabled(pictures)
