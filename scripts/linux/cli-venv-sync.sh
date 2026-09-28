#!/usr/bin/env bash
# cli-venv-sync.sh — keep the host `poindexter` CLI on the DEPLOYED code
# (Glad-Labs/glad-labs-stack#4156).
#
# Why this exists. The host CLI used to run out of a poetry venv editable-
# installed against the OPERATOR checkout (~/glad-labs-website) — i.e. whatever
# that working tree had on disk. Nothing keeps a working tree current:
# run-session.sh's ff-only pre-flight rightly refuses to merge onto uncommitted
# edits, and on 2026-09-28 it had skipped 34 runs in a row (148 commits behind).
# CLI groups that open their own pool and call service code in-process
# (`poindexter media approve/reject`, `settings`, `tasks`, …) therefore ran
# week-old service code while the containers ran main — merged fixes simply
# did not reach the operator.
#
# The fix. The CLI runs from a dedicated venv (default ~/.poindexter/cli-venv)
# whose root package is editable-installed from the DEPLOY CLONE — the tree
# deploy-checkout-sync.sh keeps at origin/main and the worker bind-mounts. Code
# needs nothing further: every CLI invocation is a fresh process importing the
# clone's current files. Only the DEPENDENCY set can drift, so that is all this
# script manages:
#
#   fingerprint = sha256(recipe, project dir, extras, pyproject.toml, poetry.lock)
#
# is stamped into <venv>/.poindexter-cli-fingerprint after a successful
# `poetry sync` AND a verification that the venv imports `poindexter` from the
# deploy clone. A stamp that differs from the clone's current fingerprint means
# stale. A failed sync never stamps, so the next caller retries.
#
# Callers (both serialize on flock(<venv>.lock)):
#   - scripts/linux/poindexter-cli.sh — the `poindexter` shim — runs `--ensure`
#     before every command: a few ms when current, one `poetry sync` (≈10 s with a
#     warm poetry cache) the first time the lockfile moves.
#   - deploy-checkout-sync.sh runs the default mode on every pass, so the venv
#     is normally re-synced before anyone next types `poindexter`.
#
# Deliberately NOT here: `docker exec` into the worker (the CLI must work when
# the worker is down, and `media open`, `game`, `backup`, `setup` and `auth`
# need the host), and fast-forwarding the operator checkout (a working tree is
# legitimately dirty for days). docs/operations/ci-deploy-chain.md has the
# full reasoning.
#
# Modes:
#   (default)  sync if stale. A host with no venv has not installed the host
#              CLI: exit 3, silently (deploy-checkout-sync runs this on every
#              host, installed or not).
#   --ensure   create the venv if missing, sync if stale. After a failed sync
#              the same fingerprint is not retried for
#              POINDEXTER_CLI_SYNC_RETRY_MIN minutes, so a box that cannot
#              reach PyPI does not pay a failing sync on every command.
#   --force    sync even when current (manual retry; ignores the backoff).
#   --check    read-only: exit 0 current, 1 stale/broken, 3 not installed.
#   --status   human-readable report on stdout.
#
# Exit codes: 0 current or synced | 1 stale, or the sync failed | 2 bad
# configuration (no deploy clone, no poetry, no python) | 3 not installed.
# Progress and errors go to STDERR only — the shim's stdout belongs to the CLI
# (`--json` output must stay parseable).
#
# Env: POINDEXTER_DEPLOY_ROOT (shared with deploy-checkout-sync + compose),
#      POINDEXTER_CLI_VENV, POINDEXTER_CLI_EXTRAS, POINDEXTER_CLI_PYTHON,
#      POINDEXTER_POETRY_BIN, POINDEXTER_CLI_SYNC_LOCK_WAIT_SEC,
#      POINDEXTER_CLI_SYNC_RETRY_MIN, POINDEXTER_CLI_SYNC_LOG.
set -uo pipefail

DEPLOY_ROOT="${POINDEXTER_DEPLOY_ROOT:-$HOME/.poindexter/deploy/glad-labs-stack}"
PROJECT_DIR="$DEPLOY_ROOT/src/cofounder_agent"
VENV="${POINDEXTER_CLI_VENV:-$HOME/.poindexter/cli-venv}"
VENV="${VENV%/}"
# The worker image installs "profiling youtube rerank pipeline qa rag". The CLI
# takes the same set minus profiling (pyroscope is a server-side sampler) and
# rerank (sentence-transformers pulls ~3 GB of CUDA torch for a retriever that
# only matters in-container). Dev dependencies are left out: this is a runtime.
EXTRAS="${POINDEXTER_CLI_EXTRAS-pipeline qa rag youtube}"
PYTHON_BIN="${POINDEXTER_CLI_PYTHON:-python3.13}"
LOCK_WAIT_SEC="${POINDEXTER_CLI_SYNC_LOCK_WAIT_SEC:-300}"
RETRY_MIN="${POINDEXTER_CLI_SYNC_RETRY_MIN:-15}"
LOG_FILE="${POINDEXTER_CLI_SYNC_LOG:-$HOME/.poindexter/cli-venv-sync.log}"
LOG_MAX_BYTES=5242880
STAMP_FILE="$VENV/.poindexter-cli-fingerprint"
LOCK_FILE="$VENV.lock"
FAIL_FILE="$VENV.sync-failed"
# Bump when the way the venv is built changes, so every installed venv
# re-syncs once without anyone having to know to run --force.
RECIPE=1

SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"

say() { echo "cli-venv-sync: $*" >&2; }
# Run a venv binary the way the CLI will see it: cwd=/ (so a poindexter/ dir in
# the caller's cwd cannot shadow the import) and no PYTHONPATH/PYTHONHOME.
venv_run() { (cd / && env -u PYTHONPATH -u PYTHONHOME "$@"); }
IMPORT_PROBE='import os, poindexter; print(os.path.dirname(os.path.realpath(poindexter.__file__)))'
log_line() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE" 2>/dev/null || true; }

usage() {
  sed -n '2,/^set -uo pipefail/p' "$SELF" | sed '$d' | sed 's/^# \{0,1\}//'
}

MODE=sync
case "${1:-}" in
  "") ;;
  --ensure) MODE=ensure ;;
  --force) MODE=force ;;
  --check) MODE=check ;;
  --status) MODE=status ;;
  -h|--help) usage; exit 0 ;;
  *) say "unknown argument '$1' (try --help)"; exit 2 ;;
esac

if [ ! -f "$PROJECT_DIR/pyproject.toml" ] || [ ! -f "$PROJECT_DIR/poetry.lock" ]; then
  say "no Poindexter project at $PROJECT_DIR — is POINDEXTER_DEPLOY_ROOT ($DEPLOY_ROOT) a deploy clone? scripts/setup-deploy-checkout.sh creates one."
  exit 2
fi
PROJECT_REAL="$(cd "$PROJECT_DIR" && pwd -P)"

fingerprint() {
  {
    printf 'recipe=%s\nproject=%s\ngroups=main\nextras=' "$RECIPE" "$PROJECT_REAL"
    # word-split on purpose: order and spacing of the extras list must not matter
    # shellcheck disable=SC2086
    printf '%s\n' $EXTRAS | sort -u | tr '\n' ' '
    printf '\n'
    sha256sum "$PROJECT_DIR/pyproject.toml" "$PROJECT_DIR/poetry.lock" | cut -d' ' -f1
  } | sha256sum | cut -c1-16
}
FP="$(fingerprint)"

# 0 current | 1 stale | 3 not installed | 4 broken (dir present, interpreter unusable)
venv_state() {
  [ -e "$VENV" ] || return 3
  [ -x "$VENV/bin/python" ] || return 4
  [ -x "$VENV/bin/poindexter" ] || return 1
  [ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$FP" ] || return 1
  return 0
}

find_python() {
  command -v "$PYTHON_BIN" 2>/dev/null
}

find_poetry() {
  if [ -n "${POINDEXTER_POETRY_BIN:-}" ]; then
    [ -x "$POINDEXTER_POETRY_BIN" ] && { echo "$POINDEXTER_POETRY_BIN"; return 0; }
    return 1
  fi
  command -v poetry 2>/dev/null && return 0
  # systemd's PATH has no ~/.local/bin (deploy-checkout-sync runs as a unit),
  # which is where pipx puts poetry — probe the standard install dirs.
  local cand
  for cand in "$HOME/.local/bin/poetry" "$HOME/.poetry/bin/poetry"; do
    [ -x "$cand" ] && { echo "$cand"; return 0; }
  done
  return 1
}

in_backoff() {
  local fp ts now
  [ -f "$FAIL_FILE" ] || return 1
  read -r fp ts < "$FAIL_FILE" || return 1
  [ "$fp" = "$FP" ] || return 1
  [[ "$ts" =~ ^[0-9]+$ ]] || return 1
  now="$(date +%s)"
  [ $(( now - ts )) -lt $(( RETRY_MIN * 60 )) ]
}

# Only ever delete something that is recognisably a venv (or an empty dir):
# POINDEXTER_CLI_VENV is operator-supplied and rm -rf takes it literally.
remove_venv() {
  [ -e "$VENV" ] || [ -L "$VENV" ] || return 0
  case "$VENV" in
    /*) ;;
    *) say "refusing to remove '$VENV': not an absolute path"; return 1 ;;
  esac
  if [ "$VENV" = "/" ] || [ "$VENV" = "$HOME" ] || [ "$VENV" = "${HOME%/}/.poindexter" ]; then
    say "refusing to remove '$VENV'"; return 1
  fi
  if [ -L "$VENV" ] || [ -f "$VENV/pyvenv.cfg" ] || [ -z "$(ls -A "$VENV" 2>/dev/null)" ]; then
    rm -rf "$VENV"
  else
    say "refusing to remove '$VENV': it is not a venv (no pyvenv.cfg). Move it aside or set POINDEXTER_CLI_VENV."
    return 1
  fi
}

verify_env() {
  local want got
  want="$(cd "$PROJECT_DIR/poindexter" 2>/dev/null && pwd -P)" || {
    say "verification failed: no poindexter package under $PROJECT_DIR"; return 1; }
  got="$(venv_run "$VENV/bin/python" -c "$IMPORT_PROBE" 2>>"$LOG_FILE")" || {
    say "verification failed: the environment cannot import poindexter (see $LOG_FILE)"; return 1; }
  if [ "$got" != "$want" ]; then
    say "verification failed: the environment imports poindexter from $got, not the deploy clone ($want)"
    return 1
  fi
  if ! venv_run "$VENV/bin/poindexter" --help >/dev/null 2>>"$LOG_FILE"; then
    say "verification failed: 'poindexter --help' exits non-zero in the synced environment (see $LOG_FILE)"
    return 1
  fi
}

do_sync() { # do_sync <state> — runs with the lock held
  local state="$1" py poetry started extra_args=() e
  poetry="$(find_poetry)" || {
    say "poetry not found (PATH, ~/.local/bin, ~/.poetry/bin); install it or set POINDEXTER_POETRY_BIN"; return 2; }
  if [ "$state" = "3" ] || [ "$state" = "4" ]; then
    py="$(find_python)" || {
      say "no '$PYTHON_BIN' interpreter to build the venv with; install it or set POINDEXTER_CLI_PYTHON"; return 2; }
    [ "$state" = "4" ] && say "$VENV exists but its interpreter is unusable; rebuilding it"
    remove_venv || return 2
    mkdir -p "$(dirname "$VENV")"
    if ! "$py" -m venv "$VENV" >>"$LOG_FILE" 2>&1; then
      say "'$py -m venv $VENV' failed (see $LOG_FILE)"; return 1
    fi
  fi
  # shellcheck disable=SC2086 — one --extras flag per word of the list
  for e in $EXTRAS; do extra_args+=(--extras "$e"); done
  started="$(date +%s)"
  say "syncing $VENV to the deployed lockfile ($PROJECT_DIR, fingerprint $FP)…"
  log_line "sync start: venv=$VENV project=$PROJECT_REAL fingerprint=$FP extras='$EXTRAS' poetry=$poetry"
  # VIRTUAL_ENV makes poetry install into THIS venv instead of minting one in
  # its cache keyed to the project path. The keyring stays out of it: this runs
  # headless under systemd, where a SecretService lookup can hang, and the
  # lockfile's sources are all public.
  local out rc=0
  out="$(mktemp "${TMPDIR:-/tmp}/cli-venv-sync.XXXXXX")" || { say "mktemp failed"; return 1; }
  env -u PYTHONPATH -u PYTHONHOME -u POETRY_ACTIVE \
    VIRTUAL_ENV="$VENV" PATH="$VENV/bin:$PATH" \
    POETRY_NO_INTERACTION=1 POETRY_KEYRING_ENABLED=false \
    "$poetry" -C "$PROJECT_DIR" sync --only main "${extra_args[@]}" --no-ansi \
    >"$out" 2>&1 || rc=$?
  cat "$out" >> "$LOG_FILE" 2>/dev/null || true
  if [ "$rc" != "0" ]; then
    say "'poetry sync' failed (exit $rc); its last lines (full log: $LOG_FILE):"
    tail -n 12 "$out" | sed 's/^/  | /' >&2
    rm -f "$out"
    return 1
  fi
  rm -f "$out"
  verify_env || return 1
  printf '%s\n' "$FP" > "$STAMP_FILE.tmp" && mv -f "$STAMP_FILE.tmp" "$STAMP_FILE" || {
    say "could not write $STAMP_FILE"; return 1; }
  rm -f "$FAIL_FILE"
  say "synced in $(( $(date +%s) - started ))s — the CLI now runs $PROJECT_REAL on its locked dependencies"
  log_line "sync ok: fingerprint=$FP"
}

state=0; venv_state || state=$?

case "$MODE" in
  check)
    [ "$state" = "4" ] && exit 1
    exit "$state" ;;
  status)
    head_sha="$(git -C "$DEPLOY_ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
    case "$state" in
      0) verdict="current" ;; 1) verdict="STALE — run: bash $SELF --force" ;;
      3) verdict="NOT INSTALLED — run: bash $(dirname "$SELF")/install-host-cli.sh" ;;
      *) verdict="BROKEN (interpreter unusable) — run: bash $SELF --force" ;;
    esac
    echo "host CLI environment: $verdict"
    echo "  venv:         $VENV"
    echo "  deploy clone: $DEPLOY_ROOT @ $head_sha"
    echo "  extras:       ${EXTRAS:-(none)}"
    echo "  fingerprint:  $FP (deploy clone)   $(cat "$STAMP_FILE" 2>/dev/null || echo none) (venv)"
    if [ -x "$VENV/bin/python" ]; then
      echo "  imports from: $(venv_run "$VENV/bin/python" -c "$IMPORT_PROBE" 2>/dev/null || echo '(import failed)')"
    fi
    if [ -f "$FAIL_FILE" ] && read -r ffp fts < "$FAIL_FILE"; then
      echo "  last failure: fingerprint $ffp at $(date -d "@$fts" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "$fts")"
    fi
    [ "$state" = "4" ] && exit 1
    exit "$state" ;;
esac

# sync | ensure | force
[ "$state" = "3" ] && [ "$MODE" = "sync" ] && exit 3
[ "$state" = "0" ] && [ "$MODE" != "force" ] && exit 0
if [ "$MODE" = "ensure" ] && [ "$state" = "1" ] && in_backoff; then
  say "the CLI environment is behind the deployed lockfile, and syncing it failed less than ${RETRY_MIN} min ago — running the deployed code on the previous dependency set. Retry now: bash $SELF --force"
  exit 1
fi

mkdir -p "$(dirname "$VENV")" "$(dirname "$LOG_FILE")"

if command -v flock >/dev/null 2>&1; then
  exec 9>>"$LOCK_FILE" || { say "cannot open $LOCK_FILE"; exit 2; }
  if ! flock -n 9; then
    say "another sync of $VENV is running; waiting up to ${LOCK_WAIT_SEC}s…"
    flock -w "$LOCK_WAIT_SEC" 9 || { say "timed out waiting for $LOCK_FILE"; exit 1; }
  fi
  # Whoever held the lock may have just done this work — or just failed at it.
  state=0; venv_state || state=$?
  [ "$state" = "0" ] && [ "$MODE" != "force" ] && exit 0
  if [ "$MODE" = "ensure" ] && [ "$state" = "1" ] && in_backoff; then
    say "a concurrent sync of this lockfile just failed — running the deployed code on the previous dependency set. Retry now: bash $SELF --force"
    exit 1
  fi
else
  say "flock not found — syncing without the concurrency lock"
fi

# single-backup rotation (poetry writes a line per package on a full build)
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -ge "$LOG_MAX_BYTES" ]; then
  mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
fi

do_sync "$state"; rc=$?
if [ "$rc" != "0" ]; then
  printf '%s %s\n' "$FP" "$(date +%s)" > "$FAIL_FILE" 2>/dev/null || true
  log_line "sync FAILED (rc=$rc): fingerprint=$FP"
  if [ -x "$VENV/bin/poindexter" ]; then
    say "the CLI keeps running the deployed code on its previous dependency set until a sync succeeds"
  fi
  exit "$rc"
fi
exit 0
