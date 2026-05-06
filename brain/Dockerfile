FROM python:3.12-slim

WORKDIR /app

# Docker CLI for restarting sibling containers via mounted socket
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker && \
    apt-get remove -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Brain has its own pyproject.toml + poetry.lock so the dep set is lockable
# and auditable independently of the worker (closes Glad-Labs/poindexter#173).
# See pyproject.toml for the rationale on keeping brain isolated from
# src/cofounder_agent. Poetry is installed transiently and uninstalled after
# resolving deps system-wide, so the runtime image only carries the deps.
COPY pyproject.toml poetry.lock /app/
RUN pip install --no-cache-dir 'poetry==2.2.1' && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction --no-ansi && \
    pip uninstall -y poetry poetry-core poetry-plugin-export 2>/dev/null || true && \
    rm -rf /root/.cache/pip /root/.cache/pypoetry

COPY brain_daemon.py health_probes.py business_probes.py bootstrap.py operator_notifier.py operator_url_probe.py migration_drift_probe.py compose_drift_probe.py prometheus_secret_writer.py probe_interface.py seed_loader.py docker_utils.py alert_sync.py alert_dispatcher.py telemetry.py secret_reader.py oauth_client.py glitchtip_triage_probe.py backup_watcher.py smart_monitor.py ./
COPY seed_app_settings.json /app/seed_app_settings.json

# brain_daemon.py imports `from brain.bootstrap import ...` so the files
# must also be accessible as a `brain.` package on PYTHONPATH. Mirror them
# into /app/brain/ with an __init__.py so both flat and package imports work.
RUN mkdir -p /app/brain && \
    cp /app/bootstrap.py /app/brain/bootstrap.py && \
    cp /app/health_probes.py /app/brain/health_probes.py && \
    cp /app/business_probes.py /app/brain/business_probes.py && \
    cp /app/operator_notifier.py /app/brain/operator_notifier.py && \
    cp /app/operator_url_probe.py /app/brain/operator_url_probe.py && \
    cp /app/migration_drift_probe.py /app/brain/migration_drift_probe.py && \
    cp /app/compose_drift_probe.py /app/brain/compose_drift_probe.py && \
    cp /app/probe_interface.py /app/brain/probe_interface.py && \
    cp /app/seed_loader.py /app/brain/seed_loader.py && \
    cp /app/docker_utils.py /app/brain/docker_utils.py && \
    cp /app/alert_sync.py /app/brain/alert_sync.py && \
    cp /app/alert_dispatcher.py /app/brain/alert_dispatcher.py && \
    cp /app/telemetry.py /app/brain/telemetry.py && \
    cp /app/secret_reader.py /app/brain/secret_reader.py && \
    cp /app/oauth_client.py /app/brain/oauth_client.py && \
    cp /app/glitchtip_triage_probe.py /app/brain/glitchtip_triage_probe.py && \
    cp /app/backup_watcher.py /app/brain/backup_watcher.py && \
    cp /app/smart_monitor.py /app/brain/smart_monitor.py && \
    touch /app/brain/__init__.py

# Non-root user with docker group (needs socket access for container restarts)
RUN groupadd -g 999 docker 2>/dev/null || true && \
    adduser --system --uid 1001 --ingroup docker --home /home/appuser appuser && \
    chown -R appuser /app
USER appuser

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import os, time; assert time.time() - os.path.getmtime('/tmp/brain_heartbeat') < 600" || exit 1

CMD ["python", "brain_daemon.py"]
