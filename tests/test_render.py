from datetime import datetime
from zoneinfo import ZoneInfo

from summarizer import config, render, transcript

from .helpers import message

PT = ZoneInfo("America/Los_Angeles")
CTX = render.Context(guild_id="111", channel_id="555", channel_name="general",
                     start=datetime(2026, 9, 20, 20, 0, tzinfo=PT),
                     end=datetime(2026, 9, 21, 20, 0, tzinfo=PT))


def script():
    return transcript.build([
        message(0, "Alice", "Raid is moving to Friday at 9pm, sign up here"),
        message(1, "Bob", "I would rather fight one horse-sized duck, obviously"),
        message(2, "Cara", "does anyone know if the server is down?"),
    ], PT)


def link(line):
    return f"https://discord.com/channels/111/555/{line.message_id}"


def summary(**overrides):
    base = {
        "tldr": "Raid moved to Friday.",
        "topics": [{
            "title": "Raid moved [Friday]",
            "summary": "Alice moved the raid.",
            "participants": ["Alice", "Bob"],
            "start_ref": "m1",
            "key_messages": [{"ref": "m3", "label": "Cara's unanswered question"},
                             {"ref": "m99", "label": "does not exist"}],
        }],
        "quotes": [{"ref": "m2", "excerpt": "one horse-sized duck, obviously"}],
    }
    return {**base, **overrides}


def test_links_are_built_from_refs_and_unknown_refs_are_dropped():
    s = script()
    [payload] = render.digest_payloads(summary(), s, CTX)
    body = payload["embeds"][0]["description"]

    # Brackets in a title would break the masked link, so they become parens.
    assert f"**[Raid moved (Friday)]({link(s.lines[0])})**" in body
    assert f"[Cara's unanswered question]({link(s.lines[2])})" in body
    assert "does not exist" not in body
    assert "*Alice, Bob*" in body


def test_topic_without_a_valid_start_ref_is_plain_bold():
    topics = [{"title": "Chatter", "summary": "Stuff.", "participants": [],
               "start_ref": "m42", "key_messages": []}]
    [payload] = render.digest_payloads(summary(topics=topics, quotes=[]), script(), CTX)
    assert "**Chatter**\nStuff." in payload["embeds"][0]["description"]
    assert "](" not in payload["embeds"][0]["description"]


def test_a_real_excerpt_is_used_and_attributed_from_the_message():
    s = script()
    [payload] = render.digest_payloads(summary(), s, CTX)
    assert (f"> “one horse-sized duck, obviously” — **Bob** [↗]({link(s.lines[1])})"
            in payload["embeds"][0]["description"])


def test_an_invented_excerpt_falls_back_to_the_real_words():
    quotes = [{"ref": "m2", "excerpt": "I love ducks more than my family"}]
    [payload] = render.digest_payloads(summary(quotes=quotes), script(), CTX)
    body = payload["embeds"][0]["description"]
    assert "more than my family" not in body
    assert "“I would rather fight one horse-sized duck, obviously”" in body


def test_header_footer_and_no_pings():
    [payload] = render.digest_payloads(summary(), script(), CTX)
    embed = payload["embeds"][0]
    assert embed["title"] == "#general — Mon, Sep 21"
    assert embed["footer"]["text"] == ("3 messages · 3 people · "
                                       "Sep 20 8:00 PM – Sep 21 8:00 PM PDT")
    assert payload["allowed_mentions"] == {"parse": []}


def test_a_long_digest_splits_between_topics_within_discord_limits():
    topics = [{"title": f"Topic {i}", "summary": "word " * 200, "participants": ["Alice"],
               "start_ref": "m1", "key_messages": []} for i in range(12)]
    payloads = render.digest_payloads(summary(topics=topics), script(), CTX)

    assert len(payloads) > 1
    for payload in payloads:
        [embed] = payload["embeds"]
        assert len(embed["description"]) <= 4096
        total = (len(embed["description"]) + len(embed.get("title", ""))
                 + len(embed.get("footer", {}).get("text", "")))
        assert total <= 6000
        assert payload["allowed_mentions"] == {"parse": []}
    assert "title" in payloads[0]["embeds"][0] and "title" not in payloads[1]["embeds"][0]
    assert "footer" in payloads[-1]["embeds"][0] and "footer" not in payloads[0]["embeds"][0]
    joined = "".join(p["embeds"][0]["description"] for p in payloads)
    assert all(f"Topic {i}]" in joined for i in range(12))


def test_quiet_note():
    note = render.quiet_payload(3, CTX)
    assert note["content"].startswith("Quiet day in <#555> — 3 messages since Sep 20 8:00 PM PDT")
    assert note["allowed_mentions"] == {"parse": []}
    assert "no messages" in render.quiet_payload(0, CTX)["content"]
    assert "1 message " in render.quiet_payload(1, CTX)["content"]


def test_failure_note_redacts_secrets(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/webhooks/1/tok")
    monkeypatch.setattr(config, "CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-secret")
    note = render.failure_payload(
        "boom at https://discord.test/webhooks/1/tok with sk-ant-oat-secret", "general")
    assert "tok" not in note["content"] and "sk-ant" not in note["content"]
    assert note["content"].startswith("⚠️ Daily summary for #general failed: boom")
    assert note["allowed_mentions"] == {"parse": []}
