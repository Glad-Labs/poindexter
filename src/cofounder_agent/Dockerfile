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

# Install Poetry (2.2.1 — matches brain/Dockerfile; 1.8.x rejects the PEP-621
# [project] table used in pyproject.toml since poetry-core>=2.0.0).
RUN pip install --no-cache-dir 'poetry==2.2.1'

# Copy project files
COPY pyproject.toml poetry.lock ./

# Install dependencies only — no root package install (--no-root).
#
# Why --no-root: three blockers make root install impossible from this build
# context (./src/cofounder_agent):
#   1. pyproject.toml declares packages = [{ include = "cofounder_agent", from = ".." }]
#      which reaches *outside* the build context — Poetry can't resolve it.
#   2. readme = "README.md" is required by the root package, but .dockerignore
#      excludes README.md (and *.md) from the build context.
#   3. Poetry 2.x's PEP-621 [project] table requires these to be resolvable at
#      install time, not just at metadata parse time.
#
# Entry-point discovery (poindexter.stages, poindexter.modules, etc.) falls
# through to the imperative _SAMPLES / Module-v1 directory-scan fallback in
# plugins/registry.py — the same path the worker image (Dockerfile.worker)
# relies on with --no-root, proven working in production.
RUN poetry config virtualenvs.create false && \
    poetry install --no-root --only main --no-interaction --no-ansi --extras "rerank"

# Fail-fast verification — confirm the rerank stack landed AND that torch is the
# CPU build (mirror of the worker image). sentence-transformers is an opt-in
# `rerank` extra as of the 2026-06-23 CI torch-trim; a dropped `[rerank]` extra
# silently degrades the cross-encoder reranker to passthrough at runtime. The
# torch.version.cuda assertion guards the CPU-trim — this image carries the
# ~2 GB-smaller CPU wheel (no GPU here), and a re-lock that re-pulls CUDA torch
# reddens the build instead of silently re-bloating it. cu128 lives in
# scripts/Dockerfile.sdxl.
RUN python -c "import sentence_transformers, torch; assert torch.version.cuda is None, f'expected CPU torch, got CUDA {torch.version.cuda}'; print('[BUILD] rerank extra OK — sentence_transformers + CPU torch', torch.__version__)"

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
