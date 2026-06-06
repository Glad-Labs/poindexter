FROM python:3.12-slim

WORKDIR /app

# Docker CLI for restarting sibling containers via mounted socket.
# Also installs the ``compose`` CLI plugin so ``docker compose -f path up -d``
# works from inside the brain — compose_drift_probe needs this for its
# auto-recover path. The bare ``docker`` static tarball only ships the
# main CLI; without the plugin, ``docker compose ...`` falls through to
# the root parser with a cryptic ``unknown shorthand flag: 'f' in -f``
# (caught 2026-05-27 by Matt's compose_drift_probe auto-recover failure).
# SHA256-pinned downloads (poindexter#631). Both artifacts are fetched
# over TLS then verified against the official published checksums BEFORE
# they are extracted / made executable — a MITM or a tampered mirror that
# swaps the bytes will fail `sha256sum -c` and abort the build instead of
# baking a poisoned docker/compose binary into the brain image.
#   - docker-27.5.1.tgz: sha256sum of the official static tarball at
#     https://download.docker.com/linux/static/stable/x86_64/ (also cross-
#     verified — this digest appears in multiple public pinned Dockerfiles).
#   - docker-compose-linux-x86_64: from the v2.33.1 release checksums.txt
#     (github.com/docker/compose/releases/download/v2.33.1/checksums.txt).
# When bumping either version, refresh the matching SHA below or the build
# fails loud.
ARG DOCKER_VERSION=27.5.1
ARG DOCKER_SHA256=4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640
ARG DOCKER_COMPOSE_VERSION=v2.33.1
ARG DOCKER_COMPOSE_SHA256=3efda1ad6caed49dedd5644cadbf7e0c9cc3d74d8844ca5237b6a43ac1ef1a46
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git smartmontools && \
    curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
        -o /tmp/docker.tgz && \
    echo "${DOCKER_SHA256}  /tmp/docker.tgz" | sha256sum -c - && \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker < /tmp/docker.tgz && \
    rm -f /tmp/docker.tgz && \
    mkdir -p /usr/local/lib/docker/cli-plugins && \
    curl -fsSL "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose && \
    echo "${DOCKER_COMPOSE_SHA256}  /usr/local/lib/docker/cli-plugins/docker-compose" | sha256sum -c - && \
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose && \
    docker compose version && \
    apt-get remove -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Brain has its own pyproject.toml + poetry.lock so the dep set is lockable
# and auditable independently of the worker (closes Glad-Labs/poindexter#173).
# See pyproject.toml for the rationale on keeping brain isolated from
# src/cofounder_agent. Poetry is installed transiently and uninstalled after
# resolving deps system-wide, so the runtime image only carries the deps.
COPY pyproject.toml poetry.lock /app/
# pyroscope-io installed via direct pip after poetry resolves the main
# deps. Reason: PR #297 originally added a `[tool.poetry.group.profiling]`
# group AND `--with profiling` here, but poetry.lock wasn't regenerated
# to include the new group. With an out-of-sync lock, `poetry install
# --with profiling` silently dropped the main group too — asyncpg
# vanished, brain crashloop with `ModuleNotFoundError: No module named
# 'asyncpg'`. Direct pip after poetry sidesteps the lock-consistency
# requirement and stays simple. Glad-Labs/poindexter#406.
RUN pip install --no-cache-dir 'poetry==2.2.1' && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction --no-ansi && \
    pip install --no-cache-dir 'pyroscope-io>=0.8.5' 'sentry-sdk>=2.0' && \
    python -c "import pyroscope; assert hasattr(pyroscope, 'configure'), 'pyroscope wheel missing configure()'; print('[BUILD] pyroscope-io OK — configure available')" && \
    python -c "import sentry_sdk; print('[BUILD] sentry-sdk OK —', sentry_sdk.VERSION)" && \
    pip uninstall -y poetry poetry-core poetry-plugin-export 2>/dev/null || true && \
    rm -rf /root/.cache/pip /root/.cache/pypoetry

COPY brain_daemon.py health_probes.py business_probes.py bootstrap.py operator_notifier.py operator_url_probe.py migration_drift_probe.py compose_drift_probe.py prometheus_secret_writer.py probe_interface.py seed_loader.py docker_utils.py alert_sync.py alert_dispatcher.py secret_reader.py oauth_client.py glitchtip_triage_probe.py prefect_stuck_flow_probe.py backup_watcher.py restore_test_probe.py smart_monitor.py docker_port_forward_probe.py pr_staleness_probe.py discord_bot_probe.py mcp_http_probe.py branch_drift_probe.py psu_power.py corsair_feed_probe.py ./
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
    cp /app/secret_reader.py /app/brain/secret_reader.py && \
    cp /app/oauth_client.py /app/brain/oauth_client.py && \
    cp /app/glitchtip_triage_probe.py /app/brain/glitchtip_triage_probe.py && \
    cp /app/backup_watcher.py /app/brain/backup_watcher.py && \
    cp /app/restore_test_probe.py /app/brain/restore_test_probe.py && \
    cp /app/smart_monitor.py /app/brain/smart_monitor.py && \
    cp /app/docker_port_forward_probe.py /app/brain/docker_port_forward_probe.py && \
    cp /app/pr_staleness_probe.py /app/brain/pr_staleness_probe.py && \
    cp /app/discord_bot_probe.py /app/brain/discord_bot_probe.py && \
    cp /app/mcp_http_probe.py /app/brain/mcp_http_probe.py && \
    cp /app/branch_drift_probe.py /app/brain/branch_drift_probe.py && \
    cp /app/psu_power.py /app/brain/psu_power.py && \
    cp /app/corsair_feed_probe.py /app/brain/corsair_feed_probe.py && \
    touch /app/brain/__init__.py

# Non-root user with docker group (needs socket access for container restarts)
RUN groupadd -g 999 docker 2>/dev/null || true && \
    adduser --system --uid 1001 --ingroup docker --home /home/appuser appuser && \
    chown -R appuser /app
USER appuser

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import os, time; assert time.time() - os.path.getmtime('/tmp/brain_heartbeat') < 600" || exit 1

CMD ["python", "brain_daemon.py"]
