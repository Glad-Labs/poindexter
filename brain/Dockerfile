FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir asyncpg

COPY brain_daemon.py health_probes.py ./

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import os, time; assert time.time() - os.path.getmtime('/tmp/brain_heartbeat') < 600" || exit 1

CMD ["python", "brain_daemon.py"]
