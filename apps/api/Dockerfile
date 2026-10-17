FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system travelpilot && useradd --system --gid travelpilot --create-home travelpilot

COPY pyproject.toml uv.lock alembic.ini ./
COPY apps ./apps
COPY packages ./packages
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev \
    && uv pip install --python .venv/bin/python --no-deps .

USER travelpilot
EXPOSE 8000
CMD ["uvicorn", "travelpilot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
