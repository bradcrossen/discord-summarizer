FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Claude Code, which writes the digest on the Max subscription. The native build
# rather than the npm package: this is a Python image, and the npm route would
# pull a whole Node runtime in behind it. `claude --version` runs in the same
# layer so a broken install fails the build here, not at 8PM.
#
# Pinned, because an unpinned install is only as new as the build cache: CI
# reuses this layer until the line changes, so it stayed on whatever version
# the first build fetched. New models need a new CLI (Opus 5.5 and Sonnet 5.5
# need 2.1.280 or later) - bump this to use one.
ARG CLAUDE_CODE_VERSION=2.1.284
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_CODE_VERSION" \
    && ln -s /root/.local/bin/claude /usr/local/bin/claude \
    && claude --version

COPY summarizer/ ./summarizer/

# TZ only sets the clock in the logs. When the digest runs is SUMMARY_TZ.
ENV CONFIG_DIR=/config \
    TZ=America/Chicago \
    SUMMARY_TIME=20:00 \
    SUMMARY_TZ=America/Los_Angeles \
    SUMMARY_MODEL=claude-opus-5-5 \
    SUMMARY_FALLBACK_MODEL=claude-sonnet-5-5 \
    PYTHONUNBUFFERED=1

# Stamped by CI with the commit being built, and logged on startup, so "is the
# running container what I just pushed?" has an answer. Kept in the last layer
# so a new SHA does not invalidate the installs above.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

CMD ["python", "-m", "summarizer"]
