# discord-summarizer

Posts a daily digest of one Discord channel. At 8PM Pacific it downloads the
last day of messages, has Claude summarize them on a **Claude Max subscription**
(no per-token bill), and posts the result to a channel through a webhook.

The digest has a TL;DR, a topic-by-topic rundown where every topic title links
to the message that started it, links to specific messages worth opening
(announcements and decisions, shared links and files, questions nobody
answered), and a few quotes of the day. A day with fewer than `MIN_MESSAGES`
gets a one-line "quiet day" note instead.

## How it works

```
8PM PT -> GET channel history (bot token) -> numbered transcript
       -> claude -p (subscription token, every tool off) -> JSON digest
       -> embeds with jump links -> POST webhook
```

- **Messages are downloaded at run time**, not collected live. Discord's REST
  API pages back through history on demand, so there is no gateway connection,
  no always-on listener and no database. The trade-off: a message deleted before
  8PM is never seen, and an edited one appears as edited.
- **Claude only ever answers in message refs** (`m12`), never URLs. The code
  turns refs into jump links and pulls quote text from the real message, so a
  link cannot be invented and a quote cannot be put in someone's mouth. An
  excerpt that is not a substring of the message is replaced by the real words.
- **The chat is untrusted input.** The `claude` subprocess runs with `--tools ""`,
  no MCP servers, and an empty HOME and working directory, so the worst a
  prompt-injection in the channel can do is make one digest wrong. Every post is
  sent with `allowed_mentions: {"parse": []}` so a digest can never ping anyone.
- **The schedule is 20:00 in `SUMMARY_TZ`**, independent of the server's clock.
  (A Central-time host shows this as 22:00 all year: both zones change DST on
  the same dates.) `/config/state.json` remembers the last digest posted, so a
  restart never double-posts, and a container that was down at 8PM catches up
  if it returns within `CATCHUP_HOURS`.

The Claude mechanism is the one [dm-assistant](https://github.com/bradcrossen/dm-assistant)
uses: shell out to `claude -p`, authenticated by `CLAUDE_CODE_OAUTH_TOKEN`.

## Setup

### 1. Discord bot (reads the channel)

A webhook can only post, so reading history needs a bot token.

1. <https://discord.com/developers/applications> -> **New Application**.
2. **Bot** tab -> **Reset Token** -> copy it. This is `DISCORD_BOT_TOKEN`.
3. Same tab, under **Privileged Gateway Intents**, enable **Message Content
   Intent**. Without it every message arrives empty; the bot detects that and
   says so in a failure note.
4. **OAuth2 -> URL Generator**: scope `bot`, permissions **View Channels** and
   **Read Message History**. Open the generated URL and add the bot to your
   server. If the channel is private, also add the bot (or its role) to it.
5. In Discord: **User Settings -> Advanced -> Developer Mode** on, then
   right-click the channel -> **Copy Channel ID**. This is `DISCORD_CHANNEL_ID`.

### 2. Webhook (posts the digest)

Channel **Edit -> Integrations -> Webhooks -> New Webhook**, name it, pick an
avatar, **Copy Webhook URL**. This is `DISCORD_WEBHOOK_URL`. It can be on the
same channel being summarized: the bot skips its own posts.

### 3. Claude subscription token

On any machine with Claude Code signed in to the Max account:

```bash
claude setup-token
```

Copy the token it prints into `CLAUDE_CODE_OAUTH_TOKEN`. The token dm-assistant
already uses works here too. It lasts about a year; when it expires the bot
posts `Daily summary for #channel failed: claude reported an error: ...` and the
fix is a new `claude setup-token`.

### 4. Unraid

Push to `main` -> GitHub Actions runs the tests and publishes
`ghcr.io/bradcrossen/discord-summarizer:latest` (plus a `:<sha>` tag for
rollback).

On Unraid, add a container from `unraid-template.xml` (copy it to
`/boot/config/plugins/dockerMan/templates-user/my-discord-summarizer.xml`, then
**Docker -> Add Container -> Template**), fill in the four required values, and
apply. There is no port and no web UI. To update: push, then on Unraid
**Force update** the container.

The log should open with:

```
summarizer INFO discord-summarizer commit=<sha> model=claude-sonnet-5 schedule=20:00 America/Los_Angeles
summarizer.scheduler INFO Next digest at 2026-09-22 20:00 PDT (2026-09-22 22:00 CDT host time)
```

To try it without waiting for 8PM, from the Unraid terminal:

```bash
docker exec discord-summarizer python -m summarizer --once --dry-run
```

```bash
docker exec discord-summarizer python -m summarizer --hours 6
```

If a day's digest went wrong, delete it in Discord and post it again for the
same 8PM-to-8PM window. This works any time before the next scheduled run; add
`--dry-run` to see it first:

```bash
docker exec discord-summarizer python -m summarizer --redo
```

Manual runs never touch the schedule's state, so they cannot suppress the real
digest.

## Running locally

```powershell
py -m pip install -r requirements-dev.txt
Copy-Item .env.example .env    # then fill it in
py -m summarizer --once --dry-run --show-transcript
py -m pytest
```

On a dev box where `claude` is already signed in, `CLAUDE_CODE_OAUTH_TOKEN` can
be left empty.

| Command | |
|---|---|
| `python -m summarizer` | run the daily schedule (the container's CMD) |
| `--once` | digest the last 24 hours now and post it |
| `--hours N` | digest the last N hours now |
| `--redo` | digest the last scheduled window (8PM to 8PM) again now |
| `--dry-run` | print the webhook payload instead of posting |
| `--show-transcript` | also print exactly what Claude is given |

## Settings

| Variable | Default | |
|---|---|---|
| `DISCORD_BOT_TOKEN` | required | reads history |
| `DISCORD_CHANNEL_ID` | required | channel to summarize |
| `DISCORD_WEBHOOK_URL` | required | where the digest goes |
| `CLAUDE_CODE_OAUTH_TOKEN` | required | from `claude setup-token`. `ANTHROPIC_API_KEY` also works if you would rather pay per token |
| `SUMMARY_TIME` | `20:00` | 24h wall-clock time of the digest |
| `SUMMARY_TZ` | `America/Los_Angeles` | zone `SUMMARY_TIME` is read in |
| `TZ` | `America/Chicago` | log timestamps only |
| `ERROR_WEBHOOK_URL` | | failure notes go here instead of the main webhook |
| `SUMMARY_MODEL` | `claude-sonnet-5` | `claude-opus-5` writes a better digest and draws harder on the subscription's usage limit |
| `SUMMARY_FALLBACK_MODEL` | `claude-sonnet-5` | tried once if `SUMMARY_MODEL` fails twice. Equal to it (the default) or blank means no extra attempt |
| `SUMMARY_TIMEOUT` | `300` | seconds per Claude call |
| `MIN_MESSAGES` | `5` | below this, a "quiet day" note and no Claude call |
| `INCLUDE_BOTS` | `0` | include other bots' messages |
| `CATCHUP_HOURS` | `3` | how late a missed digest is still worth posting |
| `MAX_TRANSCRIPT_CHARS` | `400000` | beyond this the day is digested in parts and merged |
| `MAX_PAGES` | `200` | history pages of 100; bounds a day at 20k messages |

## Choosing a model

`SUMMARY_MODEL=claude-opus-5` gives a noticeably better-written digest. Both
models were tested on the same channel and pick out the same topics, quotes and
unanswered questions; Opus phrases them better and is roughly 3-4x the usage
draw for one call a day.

The catch is that the usage limit is shared by everything signed in with this
token — dm-assistant's Ask page included — and the digest runs unattended. So a
run is attempted twice on `SUMMARY_MODEL` and then once on
`SUMMARY_FALLBACK_MODEL`: set the model to `claude-opus-5` and leave the
fallback at `claude-sonnet-5`, and an overloaded server or a spent limit costs
you a slightly plainer digest rather than the whole day. The log says which
model wrote it:

```
summarizer.claude WARNING Attempt 2 on claude-opus-5 failed (...529 Overloaded...); retrying on claude-sonnet-5
```

## Not covered yet

- **Threads.** Discord's channel-history endpoint does not return messages
  inside threads, so they are not in the digest.
- **Server nicknames.** Names are global display names; nicknames need a
  separate members lookup.
- **Multiple channels.** One container summarizes one channel. Run a second
  container for a second channel.
