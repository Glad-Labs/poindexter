# Custom Gitea Actions runner with Python 3.12, Node.js 22, and act_runner
# Base: Debian Bookworm with Python pre-installed
FROM python:3.12-bookworm

ARG ACT_RUNNER_VERSION=0.3.1

# Install Node.js 22 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm --version && node --version

# Install act_runner binary from Gitea releases
RUN curl -fsSL -o /usr/local/bin/act_runner \
      "https://gitea.com/gitea/act_runner/releases/download/v${ACT_RUNNER_VERSION}/act_runner-${ACT_RUNNER_VERSION}-linux-amd64" \
    && chmod +x /usr/local/bin/act_runner \
    && act_runner --version

# Install poetry
RUN pip install --no-cache-dir poetry \
    && poetry --version

# Confirm git is available (ships with python:bookworm)
RUN git --version

# Create startup script that registers + runs daemon
RUN echo '#!/bin/bash\n\
set -e\n\
if [ ! -f /data/.runner ]; then\n\
  act_runner register \\\n\
    --instance "$GITEA_INSTANCE_URL" \\\n\
    --token "$GITEA_RUNNER_REGISTRATION_TOKEN" \\\n\
    --name "${GITEA_RUNNER_NAME:-local-runner}" \\\n\
    --labels "${GITEA_RUNNER_LABELS:-ubuntu-latest:host}" \\\n\
    --no-interactive\n\
fi\n\
exec act_runner daemon\n\
' > /entrypoint.sh && chmod +x /entrypoint.sh

VOLUME /data
WORKDIR /data

# Drop privileges (Trivy DS-0002 / poindexter#488). act_runner does
# not need root to register or run jobs — it talks to the Gitea API
# over HTTP and shells out to Docker as the runner user when a job
# specifies a container backend. Note: Gitea was retired in this
# stack on 2026-04-30 (see CLAUDE.md). This Dockerfile is preserved
# alongside the historical compose entry but is not built in current
# CI; the USER directive future-proofs it in case it ever comes back.
RUN groupadd --system --gid 1001 runner \
 && useradd  --system --uid 1001 --gid runner --home-dir /data runner \
 && chown -R runner:runner /data
USER runner

ENTRYPOINT ["/entrypoint.sh"]
