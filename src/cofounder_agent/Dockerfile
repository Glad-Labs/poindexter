# ==================================
# GLAD LABS - AI Co-Founder Agent Dockerfile
# ==================================
# Multi-stage build for optimized production image

# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Production
FROM python:3.12-slim AS production

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1001 -s /bin/bash cofounder

# Copy Python packages from builder
COPY --from=builder /root/.local /home/cofounder/.local

# Make sure scripts in .local are usable
ENV PATH=/home/cofounder/.local/bin:$PATH

# Copy application code
COPY --chown=cofounder:cofounder . .

# Create necessary directories
RUN mkdir -p logs && chown -R cofounder:cofounder logs

# Switch to non-root user
USER cofounder

# Expose API port
EXPOSE 8000

# Set environment
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/metrics/health || exit 1

# Start application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
