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
# Build context is src/cofounder_agent (docker-compose*.yml / docker-build.yml).
COPY poindexter/brain/pyproject.toml poindexter/brain/poetry.lock /app/
# pyroscope-io installed via direct pip after poetry resolves the main
# deps. Reason: PR #297 originally added a `[tool.poetry.group.profiling]`
# group AND `--with profiling` here, but poetry.lock wasn't regenerated
# to include the new group. With an out-of-sync lock, `poetry install
# --with profiling` silently dropped the main group too — asyncpg
# vanished, brain crashloop with `ModuleNotFoundError: No module named
# 'asyncpg'`. Direct pip after poetry sidesteps the lock-consistency
# requirement and stays simple. Glad-Labs/poindexter#406.
# py-spy is a standalone Rust profiler CLI (manylinux wheel ships the binary on
# PATH — never imported by brain code). It enables a LIVE post-mortem on a hung
# daemon: `docker exec poindexter-brain-daemon py-spy dump --pid 1` prints the
# stuck native stack even when the event loop is frozen and faulthandler's own
# dump hasn't fired yet. Attaching to PID 1 needs CAP_SYS_PTRACE — granted to
# the brain service in docker-compose*.yml (2026-06-29 hang-diagnosis hardening).
RUN pip install --no-cache-dir 'poetry==2.2.1' && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction --no-ansi && \
    pip install --no-cache-dir 'pyroscope-io>=0.8.5' 'sentry-sdk>=2.0' 'py-spy>=0.4.0' && \
    python -c "import pyroscope; assert hasattr(pyroscope, 'configure'), 'pyroscope wheel missing configure()'; print('[BUILD] pyroscope-io OK — configure available')" && \
    python -c "import sentry_sdk; print('[BUILD] sentry-sdk OK —', sentry_sdk.VERSION)" && \
    py-spy --version && \
    pip uninstall -y poetry poetry-core poetry-plugin-export 2>/dev/null || true && \
    rm -rf /root/.cache/pip /root/.cache/pypoetry
# The brain is the ``poindexter.brain`` package (Glad-Labs/poindexter#1046). One
# COPY of the package plus the package root's ``__init__.py``; nothing is copied
# flat and nothing is mirrored: the container imports exactly what the tests import.
COPY poindexter/__init__.py /app/poindexter/
COPY poindexter/brain/ /app/poindexter/brain/
ENV PYTHONPATH=/app

# Non-root user with docker group (needs socket access for container restarts)
RUN groupadd -g 999 docker 2>/dev/null || true && \
    adduser --system --uid 1001 --ingroup docker --home /home/appuser appuser && \
    chown -R appuser /app
USER appuser

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import os, time; assert time.time() - os.path.getmtime('/tmp/brain_heartbeat') < 600" || exit 1

# Run as a module so the package spelling is the only one that exists.
CMD ["python", "-m", "poindexter.brain.brain_daemon"]
