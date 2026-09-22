"""Every setting, read once from the environment.

Other modules read these as ``config.NAME`` at call time rather than importing
the names, so a test can monkeypatch one without reloading anything.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """KEY=VALUE lines from a local .env, for running on a dev box.

    Real environment variables win, so this never overrides what Docker or
    Unraid set. Stdlib only: python-dotenv would be a dependency for ten lines.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


_load_dotenv(Path.cwd() / ".env")


# -- Discord -----------------------------------------------------------------

# Reading history needs a bot token: a webhook can only post. The bot needs View
# Channel + Read Message History on the source channel, and the privileged
# Message Content intent switched on in the developer portal.
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")
# Where the digest is posted. May be a webhook on the source channel itself:
# the bot's own posts are filtered out by webhook id.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
# Failure notes go here when set, otherwise to the main webhook.
ERROR_WEBHOOK_URL = os.environ.get("ERROR_WEBHOOK_URL", "")

DISCORD_API_BASE = os.environ.get("DISCORD_API_BASE", "https://discord.com/api/v10")
DISCORD_TIMEOUT = float(os.environ.get("DISCORD_TIMEOUT", "30"))
# 100 messages a page, so this bounds one day at 20k messages. A mis-set window
# cannot walk a channel's whole history.
MAX_PAGES = int(os.environ.get("MAX_PAGES", "200"))

# -- Claude ------------------------------------------------------------------

# Long-lived subscription token from `claude setup-token`. The API key is the
# pay-per-token fallback, same precedence as dm-assistant.
CLAUDE_CODE_OAUTH_TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "claude-sonnet-5")
# Wall clock for one `claude -p` call. A full day of a busy channel is a long
# prompt, so this is more generous than an interactive answer would need.
SUMMARY_TIMEOUT = float(os.environ.get("SUMMARY_TIMEOUT", "300"))
# Past this the day is summarized in parts and merged. ~4 chars a token keeps a
# part comfortably inside the context window with room for the answer.
MAX_TRANSCRIPT_CHARS = int(os.environ.get("MAX_TRANSCRIPT_CHARS", "400000"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "")

# -- Schedule ----------------------------------------------------------------

# Wall-clock time in SUMMARY_TZ, deliberately separate from the container's TZ:
# the Unraid host runs on Central and the digest is due at 8PM Pacific.
SUMMARY_TIME = os.environ.get("SUMMARY_TIME", "20:00")
SUMMARY_TZ = os.environ.get("SUMMARY_TZ", "America/Los_Angeles")
# If the container was down at the scheduled time, still post when it comes
# back within this many hours. Later than that the digest is stale news.
CATCHUP_HOURS = float(os.environ.get("CATCHUP_HOURS", "3"))

# -- Content -----------------------------------------------------------------

# Below this the day gets a one-line note instead of a Claude call.
MIN_MESSAGES = int(os.environ.get("MIN_MESSAGES", "5"))
INCLUDE_BOTS = os.environ.get("INCLUDE_BOTS", "0") == "1"

# -- Paths -------------------------------------------------------------------

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
# HOME for the claude subprocess. On the persistent volume so the CLI's own
# first-run files are written once rather than on every container start.
CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", str(CONFIG_DIR / "claude")))
STATE_PATH = CONFIG_DIR / "state.json"

GIT_SHA = os.environ.get("GIT_SHA", "unknown")


def missing(dry_run: bool = False) -> list[str]:
    """Names of required settings that are unset, for one clear startup error."""
    required = {
        "DISCORD_BOT_TOKEN": DISCORD_BOT_TOKEN,
        "DISCORD_CHANNEL_ID": DISCORD_CHANNEL_ID,
    }
    if not dry_run:
        required["DISCORD_WEBHOOK_URL"] = DISCORD_WEBHOOK_URL
    # No Claude credential is only fatal in the container. On a dev box the CLI
    # is usually already signed in, and it says so itself if it is not.
    if os.name != "nt" and not (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY):
        required["CLAUDE_CODE_OAUTH_TOKEN"] = ""
    return [name for name, value in required.items() if not value]


def secrets() -> list[str]:
    """Values that must never reach a log line or a Discord message."""
    return [s for s in (DISCORD_BOT_TOKEN, DISCORD_WEBHOOK_URL, ERROR_WEBHOOK_URL,
                        CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY) if s]
