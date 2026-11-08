# ClashHero container image.
#   docker compose up -d --build      (see docker-compose.yml)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Fonts so image cards can draw player and clan names in any script: Noto CJK for Chinese,
# Japanese and Korean, DejaVu for Arabic, Cyrillic, Greek and symbols. FriBiDi turns on Pillow's
# text shaping, so Arabic names come out joined and right-to-left.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-dejavu-core libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 1000 clashhero \
    && mkdir -p /app/data && chown clashhero /app/data

USER clashhero
VOLUME /app/data
CMD ["clashhero"]
