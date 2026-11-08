# ClashHero container image.
#   docker compose up -d --build      (see docker-compose.yml)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 1000 clashhero \
    && mkdir -p /app/data && chown clashhero /app/data

USER clashhero
VOLUME /app/data
CMD ["/app/.venv/bin/clashhero"]
