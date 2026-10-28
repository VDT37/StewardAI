# The public demo (Phase 3 Part G, P3-D3 A): Steward in demo mode over the bundled recordings in
# deploy/demo. The image holds no key, no pass and no owner token, and the container is started
# without the .env file. Build from the repository root after building the console
# (apps/web/dist), then run it read-only, published on loopback only, behind Caddy:
#
#   docker build -f deploy/demo/Dockerfile -t steward-demo:local .
#   docker run -d --name steward-demo --restart unless-stopped --read-only --tmpfs /tmp \
#     --cap-drop ALL --security-opt no-new-privileges -p 127.0.0.1:8100:8100 steward-demo:local
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/gateway apps/gateway
COPY packages packages
RUN uv sync --locked --no-dev --package steward-gateway

COPY config config
COPY deploy/demo deploy/demo
COPY apps/web/dist apps/web/dist

RUN useradd --system --uid 10001 --no-create-home steward
USER steward
EXPOSE 8100
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4)"
CMD ["/app/.venv/bin/steward", "demo", "serve", "--host", "0.0.0.0", "--port", "8100"]
