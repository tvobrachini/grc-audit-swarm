# ─── Builder stage: resolve and install dependencies with uv ─────────────────
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install uv.
RUN pip install --no-cache-dir uv

# Copy project definition files first for better layer caching.
COPY pyproject.toml uv.lock ./

# Every dependency in uv.lock ships a manylinux wheel for cp312, so no C
# toolchain (build-essential) or sqlite3 CLI is required to build the venv.
RUN uv sync --frozen --no-dev --no-install-project

# Copy the rest of the application and install it into the venv.
COPY . .
RUN uv sync --frozen --no-dev

# ─── Runtime stage: slim image, non-root user ─────────────────────────────────
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY --from=builder /app /app

RUN mkdir -p /app/data && chown -R app:app /app

USER app

EXPOSE 8000

# Runs the FastAPI backend; the React UI is served by the frontend image.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
