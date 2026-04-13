FROM python:3.12-slim

WORKDIR /app

# Docker CLI for restarting sibling containers via mounted socket
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker && \
    apt-get remove -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir asyncpg

COPY brain_daemon.py health_probes.py ./

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import os, time; assert time.time() - os.path.getmtime('/tmp/brain_heartbeat') < 600" || exit 1

CMD ["python", "brain_daemon.py"]
