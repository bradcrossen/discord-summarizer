import asyncio
import json
import os
import sys
from zoneinfo import ZoneInfo

import pytest

from summarizer import claude, config, transcript

from .helpers import message

GOOD = {"tldr": "A day.", "topics": [], "quotes": []}


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CLAUDE_HOME", tmp_path / "claude")
    monkeypatch.setattr(config, "CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "api-key")
    monkeypatch.setattr(config, "SUMMARY_TIMEOUT", 20.0)


def fake_cli(monkeypatch, tmp_path, body: str) -> None:
    """A stand-in `claude` that really is spawned, fed stdin and read back."""
    script = tmp_path / "fake_claude.py"
    script.write_text("import json, sys, time\nprompt = sys.stdin.read()\n" + body,
                      encoding="utf-8")
    monkeypatch.setattr(claude, "build_command", lambda: [sys.executable, str(script)])


def test_command_has_no_tools_no_mcp_and_no_transcript():
    cmd = claude.build_command()
    assert cmd[1] == "-p"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert {"--strict-mcp-config", "--disable-slash-commands",
            "--no-session-persistence"} <= set(cmd)
    assert "--mcp-config" not in cmd and "--allowedTools" not in cmd
    assert cmd[cmd.index("--model") + 1] == config.SUMMARY_MODEL
    assert os.path.isfile(cmd[cmd.index("--system-prompt-file") + 1])
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["required"] == [
        "tldr", "topics", "quotes"]


def test_subscription_token_wins_over_the_api_key():
    env = claude.subprocess_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token"
    assert "ANTHROPIC_API_KEY" not in env or os.name == "nt"
    assert env["DISABLE_AUTOUPDATER"] == "1"


@pytest.mark.skipif(os.name == "nt", reason="the dev box inherits its environment")
def test_container_env_is_minimal(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "must-not-leak")
    env = claude.subprocess_env()
    assert set(env) == {"HOME", "PATH", "TZ", "TERM", "NO_COLOR",
                        "DISABLE_AUTOUPDATER", "CLAUDE_CODE_OAUTH_TOKEN"}
    assert env["HOME"] == str(config.CLAUDE_HOME)


def test_parse_prefers_structured_output():
    line = {"is_error": False, "result": "prose", "structured_output": GOOD}
    assert claude.parse_result(json.dumps(line).encode()) == GOOD


def test_parse_falls_back_to_fenced_json_text():
    line = {"is_error": False, "result": "```json\n" + json.dumps(GOOD) + "\n```"}
    assert claude.parse_result(json.dumps(line).encode()) == GOOD


def test_parse_surfaces_cli_errors():
    line = {"is_error": True, "result": "Invalid API key · Please run /login"}
    with pytest.raises(claude.ClaudeError, match="Please run /login"):
        claude.parse_result(json.dumps(line).encode())
    with pytest.raises(claude.ClaudeError, match="not JSON"):
        claude.parse_result(b"Segmentation fault")
    with pytest.raises(claude.ClaudeError, match="not the JSON digest"):
        claude.parse_result(json.dumps({"result": "Sorry, I cannot."}).encode())


def test_validate_drops_malformed_entries():
    messy = {"tldr": "ok", "topics": [{"title": "T", "summary": "S"}, "junk", {"title": 1}],
             "quotes": [{"ref": "m1", "excerpt": "x"}, None]}
    clean = claude.validate(messy)
    assert clean["topics"] == [{"title": "T", "summary": "S"}]
    assert clean["quotes"] == [{"ref": "m1", "excerpt": "x"}]
    with pytest.raises(claude.ClaudeError):
        claude.validate({"topics": []})


def test_run_feeds_the_prompt_on_stdin(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, (
        "print(json.dumps({'is_error': False, 'structured_output': "
        "{'tldr': prompt, 'topics': [], 'quotes': []}}))\n"))
    got = asyncio.run(claude.run_claude("héllo <transcript>…</transcript>"))
    assert got["tldr"] == "héllo <transcript>…</transcript>"
    assert (config.CLAUDE_HOME / "workspace").is_dir()


def test_run_times_out_and_kills_the_child(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SUMMARY_TIMEOUT", 0.5)
    fake_cli(monkeypatch, tmp_path, "time.sleep(30)\n")
    with pytest.raises(claude.ClaudeError, match="did not answer"):
        asyncio.run(claude.run_claude("x"))


def test_run_reports_a_silent_crash(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, "sys.stderr.write('kaboom'); sys.exit(3)\n")
    with pytest.raises(claude.ClaudeError, match="exited 3.*kaboom"):
        asyncio.run(claude.run_claude("x"))


def _script(n):
    return transcript.build([message(i, "Alice", "x" * 80) for i in range(n)],
                            ZoneInfo("America/Los_Angeles"))


def test_summarize_retries_once(monkeypatch):
    calls = []

    async def flaky(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise claude.ClaudeError("overloaded")
        return GOOD

    async def no_sleep(_):
        pass

    monkeypatch.setattr(claude, "run_claude", flaky)
    monkeypatch.setattr(claude.asyncio, "sleep", no_sleep)
    assert asyncio.run(claude.summarize("Channel: #general", _script(3))) == GOOD
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0].startswith("Channel: #general\n\n<transcript>\n[m1] ")


def test_a_huge_day_is_digested_in_parts_then_merged(monkeypatch):
    monkeypatch.setattr(config, "MAX_TRANSCRIPT_CHARS", 1000)
    prompts = []

    async def fake(prompt):
        prompts.append(prompt)
        return {"tldr": f"part {len(prompts)}", "topics": [], "quotes": []}

    monkeypatch.setattr(claude, "run_claude", fake)
    script = _script(30)
    asyncio.run(claude.summarize("Channel: #general", script))

    *parts, merge = prompts
    assert len(parts) >= 2
    assert "part 1 of" in parts[0] and "[m1] " in parts[0]
    assert "[m30] " in parts[-1] and "[m30] " not in parts[0]
    assert "<parts>" in merge and '"part 1"' in merge and "<transcript>" not in merge
