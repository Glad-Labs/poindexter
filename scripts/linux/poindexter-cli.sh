#!/usr/bin/env bash
# poindexter — host launcher for the Poindexter CLI. Runs the DEPLOYED code.
# (Glad-Labs/glad-labs-stack#4156)
#
# Installed by scripts/linux/install-host-cli.sh as a SYMLINK:
#
#   ~/.local/bin/poindexter -> <deploy clone>/scripts/linux/poindexter-cli.sh
#
# so this launcher, the venv-sync logic it calls, and the CLI code itself all
# come from the deploy clone that deploy-checkout-sync.sh keeps at origin/main.
# A merged change to any of the three reaches the operator on the next sync
# pass, with nothing to reinstall.
#
# Every call:
#   1. `cli-venv-sync.sh --ensure` — a few-ms fingerprint check when the CLI
#      venv matches the deployed poetry.lock; a `poetry sync` when it does not
#      (progress on stderr). A failed sync never blocks the command: it runs on
#      the previous dependency set with a warning, and retries later.
#   2. exec ~/.poindexter/cli-venv/bin/poindexter, whose `poindexter` package is
#      editable-installed from the deploy clone.
#
# It never falls back to another tree. The previous launcher picked the newest
# poetry venv under ~/.cache/pypoetry/virtualenvs, which was editable-installed
# against the operator's working checkout. That checkout sat 148 commits behind
# main with nothing reporting it, and in-process commands ran its service code.
#
# To try a BRANCH's CLI code (e.g. from a worktree), put its package dir first:
#   PYTHONPATH=<checkout>/src/cofounder_agent poindexter …
# The code comes from the checkout, the dependencies stay the deployed set, and
# this launcher says so on stderr. Or run that checkout's own venv's
# bin/poindexter directly.
#
# Commands that talk to the Ollama fleet or write embeddings still belong in
# the worker container (`docker exec poindexter-worker python -m poindexter …`):
# app_settings points them at host.docker.internal, which resolves only there.
#
# Env: POINDEXTER_DEPLOY_ROOT, POINDEXTER_CLI_VENV (both shared with
#      cli-venv-sync.sh), POINDEXTER_API_URL (default http://localhost:8002),
#      POINDEXTER_CLI_NO_SYNC=1 (skip step 1 — emergencies only).
set -u

deploy_root="${POINDEXTER_DEPLOY_ROOT:-$HOME/.poindexter/deploy/glad-labs-stack}"
venv="${POINDEXTER_CLI_VENV:-$HOME/.poindexter/cli-venv}"
venv="${venv%/}"
sync="$deploy_root/scripts/linux/cli-venv-sync.sh"

if [ ! -f "$sync" ]; then
  if [ -e "$deploy_root/.git" ]; then
    echo "poindexter: the deploy clone at $deploy_root predates the host-CLI launcher (no $sync)." >&2
    echo "  Bring it to origin/main: systemctl start poindexter-deploy-sync.service" >&2
  else
    echo "poindexter: no deploy clone at $deploy_root — the host CLI runs the deployed code from it." >&2
    echo "  Create it with scripts/setup-deploy-checkout.sh, or point POINDEXTER_DEPLOY_ROOT at yours." >&2
  fi
  exit 127
fi

if [ "${POINDEXTER_CLI_NO_SYNC:-0}" != "1" ]; then
  # stdout -> stderr: the sync never writes to stdout, and this makes sure the
  # CLI's own stdout (`--json`, piped output) is the only thing that does.
  bash "$sync" --ensure 1>&2 || true
fi

if [ ! -x "$venv/bin/poindexter" ]; then
  echo "poindexter: no CLI environment at $venv." >&2
  echo "  Build it: bash $sync --ensure   (log: ~/.poindexter/cli-venv-sync.log)" >&2
  exit 127
fi

# PYTHONPATH outranks the venv's editable install. Honour it — pointing it at a
# worktree is a legitimate way to try a branch — but never silently.
if [ -n "${PYTHONPATH:-}" ]; then
  IFS=: read -r -a _pp <<< "$PYTHONPATH"
  for _entry in "${_pp[@]}"; do
    if [ -n "$_entry" ] && [ -f "$_entry/poindexter/__init__.py" ]; then
      echo "poindexter: running the poindexter package from PYTHONPATH ($_entry), not the deploy clone" >&2
      break
    fi
  done
fi

# The product ships no hardcoded API URL (#198), so the client CLI is told where
# the worker lives: the worker container publishes :8002 on localhost. Export
# your own POINDEXTER_API_URL first to reach another box on the tailnet.
export POINDEXTER_API_URL="${POINDEXTER_API_URL:-http://localhost:8002}"

exec "$venv/bin/poindexter" "$@"
