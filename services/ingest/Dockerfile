FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services/api ./services/api
COPY services/ingest ./services/ingest

RUN uv sync --frozen --package polypredictor-ingest --no-dev

CMD ["python", "-m", "ingest.workers.gamma_discovery"]
