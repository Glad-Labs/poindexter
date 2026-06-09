#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Poindexter — Dedicated Deploy Checkout Setup
# Built by Glad Labs LLC
#
# Creates the DEDICATED deploy checkout the migration-drift probe resyncs
# during genuine self-heal (Glad-Labs/poindexter#228). The brain container
# bind-mounts this checkout at /host-deploy (RW) and, when
# app_settings.migration_drift_auto_sync_enabled=true, runs
# `git reset --hard origin/main` + `git clean -fd` here BEFORE restarting
# the worker — so the restart applies correct, un-polluted migration files
# rather than restarting blindly into a stale/polluted working tree (the
# 2026-06-07 drift storm root cause).
#
# Why a SEPARATE checkout (not the live worker checkout)?
#   The probe's recovery is a hard reset. Doing that on the checkout where
#   you (or a scheduled agent) might have uncommitted work would clobber it
#   and create a "is work active?" race. A dedicated checkout that NOTHING
#   else ever touches collapses that race: reset --hard is always safe.
#
# Usage:
#   bash scripts/setup-deploy-checkout.sh                 # from repo root
#   DEPLOY_DIR=/custom/path bash scripts/setup-deploy-checkout.sh
#
# Idempotent: re-running fetches + reports status instead of re-cloning.
#
# After running this:
#   1. Recreate the brain so the /host-deploy mount attaches:
#        docker compose -f docker-compose.local.yml up -d --force-recreate brain-daemon
#   2. (Optional) schedule a periodic `git fetch origin` in this checkout so
#      origin/main stays current for the probe to reset to (see runbook).
#   3. Flip the flag to enable genuine self-heal:
#        poindexter set migration_drift_auto_sync_enabled true
#
# NOTE: This file MUST be checked out with LF line endings (.gitattributes
# `*.sh eol=lf` enforces it). CRLF makes bash choke on `set -o pipefail\r`.
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────
# Host path that docker-compose.local.yml bind-mounts to /host-deploy.
# Keep in sync with the brain-daemon volumes block + the in-container path
# in app_settings.migration_drift_deploy_checkout_path (default /host-deploy).
DEPLOY_DIR="${DEPLOY_DIR:-${HOME}/.poindexter/deploy/glad-labs-stack}"

# The deploy checkout tracks the SAME source of truth the worker runs from:
# origin = Glad-Labs/poindexter (private full tree). We derive the URL
# from THIS repo's `origin` remote so the script never hardcodes a URL and
# works for any fork/operator.
SOURCE_REMOTE="${SOURCE_REMOTE:-origin}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"

log() { printf '[setup-deploy-checkout] %s\n' "$1"; }

# ── Resolve the source URL from the current repo ──────────────────────
if ! command -v git >/dev/null 2>&1; then
  log "ERROR: git is not on PATH." >&2
  exit 1
fi

SOURCE_URL="$(git remote get-url "${SOURCE_REMOTE}" 2>/dev/null || true)"
if [[ -z "${SOURCE_URL}" ]]; then
  log "ERROR: could not resolve '${SOURCE_REMOTE}' remote URL from $(pwd)." >&2
  log "       Run this from a glad-labs-stack checkout, or set SOURCE_REMOTE." >&2
  exit 1
fi
log "Source remote '${SOURCE_REMOTE}' = ${SOURCE_URL}"
log "Deploy checkout dir = ${DEPLOY_DIR}"

# ── Clone (first run) or fetch (subsequent runs) ──────────────────────
mkdir -p "$(dirname "${DEPLOY_DIR}")"

if git -C "${DEPLOY_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "Deploy checkout already exists — fetching ${SOURCE_REMOTE}/${SYNC_BRANCH}…"
  git -C "${DEPLOY_DIR}" fetch "${SOURCE_REMOTE}" "${SYNC_BRANCH}" --prune
else
  if [[ -e "${DEPLOY_DIR}" && -n "$(ls -A "${DEPLOY_DIR}" 2>/dev/null || true)" ]]; then
    log "ERROR: ${DEPLOY_DIR} exists, is non-empty, and is not a git work tree." >&2
    log "       Remove or empty it, then re-run." >&2
    exit 1
  fi
  log "Cloning ${SOURCE_URL} → ${DEPLOY_DIR} (branch ${SYNC_BRANCH})…"
  git clone --branch "${SYNC_BRANCH}" --origin "${SOURCE_REMOTE}" \
    "${SOURCE_URL}" "${DEPLOY_DIR}"
fi

# ── Pin the working tree to the freshly-fetched branch tip ────────────
# This mirrors exactly what the probe does, so the operator can verify the
# end state immediately. Safe: dedicated checkout, nothing else touches it.
git -C "${DEPLOY_DIR}" reset --hard "${SOURCE_REMOTE}/${SYNC_BRANCH}"
git -C "${DEPLOY_DIR}" clean -fd

HEAD_SHA="$(git -C "${DEPLOY_DIR}" rev-parse --short HEAD)"
log "Deploy checkout ready at HEAD ${HEAD_SHA} (${SOURCE_REMOTE}/${SYNC_BRANCH})"
log ""
log "Next steps:"
log "  1. docker compose -f docker-compose.local.yml up -d --force-recreate brain-daemon"
log "  2. poindexter set migration_drift_auto_sync_enabled true   # enable self-heal"
log "  3. (optional) schedule a periodic 'git -C ${DEPLOY_DIR} fetch ${SOURCE_REMOTE} ${SYNC_BRANCH}'"
log "See docs/operations/migration-drift-self-heal.md for the full runbook."
