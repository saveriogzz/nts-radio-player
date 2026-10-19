# Source router: watches the PipeWire graph (pw-dump) to see which source is
# playing, decides who owns the speaker, and serves a local WebSocket API.
# Built from the repo root so it can reuse pyproject.toml / uv.lock.

FROM python:3.11-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PYTHON=/usr/local/bin/python3 UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Only the router's own dependency group, not the app's
RUN uv sync --locked --only-group router --no-cache

FROM python:3.11-slim-bookworm

# pw-dump, plus the PipeWire client config (client.conf) it needs to connect
RUN apt-get update \
  && apt-get install -y --no-install-recommends pipewire pipewire-bin \
  && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY router/ ./router/

ENV PIPEWIRE_RUNTIME_DIR=/run/pipewire
ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "router"]
