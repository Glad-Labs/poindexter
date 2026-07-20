#!/usr/bin/env bash
# backup-precious.sh — pre-migration backup of the precious + useful tiers.
#
# Run on the CURRENT box while the Docker stack is up. Works from Git Bash on
# Windows or any Linux shell.
#   bash scripts/linux/backup-precious.sh /d/migration-backup
#
# Design notes (learned the hard way on a live run):
#   * Container, volume and database names are DISCOVERED, never hardcoded.
#     Compose derives volume names from the project directory, so a hardcoded
#     list silently backs up nothing on a differently-named checkout.
#   * Every Postgres instance gets pg_dump'd — the stack runs more than one
#     (the app DB plus whatever bundled services bring their own).
#   * Postgres data volumes are NEVER tarred. A tar of a live data directory is
#     a torn snapshot that may not replay; the pg_dump is the real artifact.
#   * Each dump is verified readable with `pg_restore --list` before we claim
#     success. An unverified dump is a hypothesis, not a backup.
#   * Git Bash mangles docker's `-v src:dst` arguments (see MSYS_NO_PATHCONV
#     below). This silently produced ZERO volume backups on a live run.
set -euo pipefail

# --- Git Bash / MSYS path-conversion guard ----------------------------------
# On Windows, MSYS rewrites Unix-looking arguments into Windows paths before
# exec. It mangles docker's volume syntax: `-v "$OUT/volumes:/b"` is rewritten
# so the ":/b" becomes ";B", and docker then creates a HOST DIRECTORY literally
# named "volumes;B" while the container writes into a path that disappears with
# the container. The tar reports success and you end up with an empty volumes/.
# Observed for real on 2026-07-19: 0 of 6 tarballs, plus a stray "volumes;B".
# MSYS_NO_PATHCONV=1 is scoped to each docker invocation rather than exported
# globally, because the surrounding shell still needs normal path handling.
DOCKER_ENV=""
if [ -n "${MSYSTEM:-}" ] || uname -s 2>/dev/null | grep -qiE 'mingw|msys'; then
  DOCKER_ENV="MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL=*"
  echo "note: Git Bash detected — disabling MSYS path conversion for docker args"
fi
# Run docker with path conversion disabled where required.
dk() { env $DOCKER_ENV docker "$@"; }

DEST="${1:?usage: backup-precious.sh <dest-dir>}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/poindexter-backup-$STAMP"
mkdir -p "$OUT/pg" "$OUT/volumes" "$OUT/config"

# Databases that are disposable by construction — unit/e2e scratch DBs.
JUNK_DB_RE='(_unit_|_test$|^test|e2e|flatten_)'

echo "== Discovering Postgres containers =="
mapfile -t PG_CONTAINERS < <(
  dk ps --format '{{.Names}}|{{.Image}}' |
  awk -F'|' '$2 ~ /postgres|pgvector/ && $2 !~ /exporter/ { print $1 }'
)
[ "${#PG_CONTAINERS[@]}" -gt 0 ] || { echo "FATAL: no Postgres containers running"; exit 1; }
printf '  - %s\n' "${PG_CONTAINERS[@]}"

for c in "${PG_CONTAINERS[@]}"; do
  # The official image records its superuser here; fall back to the conventional default.
  user="$(dk exec "$c" printenv POSTGRES_USER 2>/dev/null || echo postgres)"
  echo "== $c (superuser: $user) =="

  # Roles and passwords live outside any single database.
  dk exec "$c" pg_dumpall -U "$user" --globals-only > "$OUT/pg/${c}__globals.sql"

  mapfile -t dbs < <(
    dk exec "$c" psql -U "$user" -d postgres -t -A -c \
      "select datname from pg_database where datallowconn and not datistemplate order by 1;" |
    tr -d '\r'
  )

  for db in "${dbs[@]}"; do
    [ -n "$db" ] || continue
    if [[ "$db" =~ $JUNK_DB_RE ]]; then echo "  skip $db (scratch)"; continue; fi
    # Dump to a file INSIDE the container, then docker cp it out. Redirecting a
    # binary -Fc stream through the host shell corrupts it on some platforms.
    dk exec "$c" pg_dump -U "$user" -Fc -f "/tmp/${db}.dump" "$db"
    dk cp "$c:/tmp/${db}.dump" "$OUT/pg/${c}__${db}.dump"
    dk exec "$c" rm -f "/tmp/${db}.dump"
    sz=$(du -m "$OUT/pg/${c}__${db}.dump" | cut -f1)
    echo "  dumped $db (${sz} MB)"
  done
done

echo "== Verifying every dump is readable =="
fail=0
for d in "$OUT"/pg/*.dump; do
  if pg_restore --list "$d" >/dev/null 2>&1 ||
     dk exec -i "${PG_CONTAINERS[0]}" pg_restore --list /dev/stdin < "$d" >/dev/null 2>&1; then
    echo "  OK   $(basename "$d")"
  else
    echo "  FAIL $(basename "$d") — table of contents unreadable"; fail=1
  fi
done
[ "$fail" -eq 0 ] || { echo "FATAL: at least one dump is unreadable. Do not proceed."; exit 1; }

echo "== Useful volumes (tar) =="
# Discover volumes actually attached to running containers, minus Postgres data
# dirs (already captured as dumps above) and anything trivially regenerable.
mapfile -t VOLS < <(
  dk ps -q |
  xargs -r docker inspect -f '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}' |
  grep -v '^$' | sort -u |
  grep -Ev '(postgres|pg-data|_db-data|db-data$)'
)
[ "${#VOLS[@]}" -gt 0 ] || echo "  (no non-database volumes attached to running containers)"
for vol in "${VOLS[@]}"; do
  [ -n "$vol" ] || continue
  # Stream the tar to the HOST via stdout instead of bind-mounting an output
  # directory. One less path for MSYS to mangle, and it works identically on
  # Linux and Git Bash. (bash redirects binary faithfully — unlike PowerShell,
  # whose `>` corrupts binary streams.)
  #
  # --warning=no-file-changed: live services mutate files mid-read. Expected for
  # observability data, not a failure — but real errors still surface, and the
  # assertion below is what actually decides whether we trust the artifact.
  #
  # Count the volume's real files BEFORE tarring. This is the only way to tell
  # "this volume is genuinely empty" from "the mount failed and we archived
  # nothing" — a size threshold cannot separate those two, and both produce a
  # valid, tiny gzip. That ambiguity is the documented failure mode in
  # Glad-Labs/poindexter#890 (three 0-byte tarballs sat undetected since May).
  src_files=$(dk run --rm -v "$vol:/v:ro" alpine \
                sh -c 'find /v -type f 2>/dev/null | wc -l' 2>/dev/null | tr -cd '0-9')
  if dk run --rm -v "$vol:/v:ro" alpine \
       tar czf - --warning=no-file-changed -C /v . > "$OUT/volumes/$vol.tar.gz"; then
    sz=$(du -m "$OUT/volumes/$vol.tar.gz" 2>/dev/null | cut -f1)
    # Non-directory entries actually inside the archive.
    tar_files=$(tar -tzf "$OUT/volumes/$vol.tar.gz" 2>/dev/null | grep -cv '/$' || true)
    # NOT a megabyte floor. Real volumes are legitimately tiny — measured on a
    # live stack, gladlabs-pgadmin-data is 17 KB (11 session files) and
    # gladlabs-postiz-uploads is 27 KB (2 images). An earlier revision of this
    # script asserted ">= 1 MB" and would have failed both of those valid
    # archives while still passing anything that happened to exceed the floor.
    # File count is the signal that actually distinguishes captured from missed.
    if [ -z "${src_files}" ]; then
      echo "  ? $vol (${sz:-0} MB, ${tar_files} files) — could not count source files; verify by hand"
      fail=1
    elif [ "${src_files}" -eq 0 ] && [ "${tar_files}" -eq 0 ]; then
      echo "  - $vol — volume is genuinely EMPTY (0 files at source). Archive is correct."
    elif [ "${tar_files}" -eq 0 ]; then
      echo "  ! $vol — source has ${src_files} files but the archive has NONE. FAILED, not empty."
      fail=1
    else
      echo "  - $vol (${sz:-0} MB, ${tar_files}/${src_files} files)"
    fi
  else
    echo "  ! $vol — tar reported an error, inspect before trusting this artifact"
    fail=1
  fi
done
[ "$fail" -eq 0 ] || echo "WARNING: at least one volume did not back up cleanly (see ! lines above)."

echo "== Operator config =="
# Top-level files only: bootstrap.toml, tokens, PEMs. The bulk subdirectories
# (local DB backups, generated media, venvs, caches, worktrees) are either
# regenerable or already superseded by the dumps above — copying them turns a
# ~1 MB config backup into tens of GB.
mkdir -p "$OUT/config/dot-poindexter"
find "$HOME/.poindexter" -maxdepth 1 -type f ! -name '*.log' ! -name '*.log.[0-9]' \
  -exec cp {} "$OUT/config/dot-poindexter/" \;
[ -f "$OUT/config/dot-poindexter/bootstrap.toml" ] || {
  echo "FATAL: bootstrap.toml did not land in the backup — it is the only"
  echo "       irreplaceable file here (master key + DB URL). Do not proceed."
  exit 1
}
cp -r "$HOME/.claude" "$OUT/config/dot-claude" 2>/dev/null || \
  echo "  (note: some ~/.claude files were locked or dangling; memory/ is what matters)"

echo "$OUT" > "$DEST/LATEST-BACKUP.txt"
echo
echo "DONE -> $OUT  ($(du -sm "$OUT" | cut -f1) MB)"
echo "Restore rehearsal is NOT optional. Before wiping anything, restore the"
echo "app-database dump into a throwaway container and compare row counts."
