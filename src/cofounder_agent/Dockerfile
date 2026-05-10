FROM python:3.13-slim

WORKDIR /app

# Install system dependencies.
# The chromium deps are here so that playwright's bundled browser can
# launch inside the container for the preview-screenshot QA reviewer.
# If the screenshot check is disabled via app_settings (default), these
# are just cold bytes on disk and the rest of the pipeline is unaffected.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.5

# Copy project files
COPY pyproject.toml poetry.lock ./

# Install dependencies only (--no-root skips installing the project package itself,
# avoiding README.md not found errors in the container)
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main --no-root

# Install Playwright's bundled chromium. This downloads ~150MB into
# /root/.cache/ms-playwright/ during build. We skip the OS deps install
# step (--with-deps would re-pin apt packages) because we already
# installed the required libs above.
RUN python -m playwright install chromium

# Copy application code
COPY . .

# Create non-root user with home directory
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup --home /home/appuser appuser

# Ensure appuser owns the application directory
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/api/health || exit 1

# Run the application - use shell form to expand $PORT environment variable
CMD poetry run uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
