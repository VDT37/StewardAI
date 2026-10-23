# Personal agent image: OpenClaw on Node 24, run as the non-root node user (uid 1000).
# Build on the VM: docker build -t steward-openclaw:local agents/openclaw
FROM node:24-bookworm-slim

# OpenClaw needs Node >=24.16 on the 24 line.
ARG OPENCLAW_VERSION=2026.10.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# npm install does not run the onboarding wizard.
RUN npm install -g "openclaw@${OPENCLAW_VERSION}" && npm cache clean --force

USER node
WORKDIR /home/node
ENV HOME=/home/node
# The policy denies writes to /var/tmp, and SQLite would try it before /tmp.
ENV TMPDIR=/tmp SQLITE_TMPDIR=/tmp

COPY --chown=node:node openclaw.json /home/node/.openclaw/openclaw.json

# Fail the build if the CLI is missing, and print the Node binary the personal profile allows.
RUN node --version \
    && openclaw --version \
    && echo "node binary: $(readlink -f "$(command -v node)")"
