# OmniMemory API Dockerfile
FROM python:3.12-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install system dependencies for Pillow/image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libheif-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy all source files
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations

# Create virtual environment and install dependencies
RUN uv venv && uv sync --no-dev

# Expose port
EXPOSE 8000

# Run migrations on startup, then start uvicorn
CMD ["sh", "-c", "uv run python -m app.db.migrator && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
