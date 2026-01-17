FROM python:3.13-slim

WORKDIR /app

# Install system dependencies (including headless graphics libs if needed by indirect deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.7.1

# Copy project files
COPY pyproject.toml poetry.lock ./

# Install dependencies (--no-root to skip installing the package itself yet)
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main

# Copy application code
COPY . .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Run the application - use shell form to expand $PORT environment variable
CMD poetry run uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
