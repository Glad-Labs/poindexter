FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir asyncpg

COPY brain_daemon.py health_probes.py ./

CMD ["python", "brain_daemon.py"]
