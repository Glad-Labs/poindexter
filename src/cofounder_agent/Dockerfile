FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (including headless graphics libs if needed by indirect deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.5

# Copy project files
COPY pyproject.toml poetry.lock ./

# Install dependencies only (--no-root skips installing the project package itself,
# avoiding README.md not found errors in the container)
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main --no-root

# Copy application code
COPY . .

# Health check — use /api/health to match Railway's healthcheckPath in railway.json
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/api/health || exit 1

# Run the application - use shell form to expand $PORT environment variable
CMD poetry run uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
