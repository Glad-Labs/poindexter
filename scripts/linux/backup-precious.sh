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
set -euo pipefail

DEST="${1:?usage: backup-precious.sh <dest-dir>}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/poindexter-backup-$STAMP"
mkdir -p "$OUT/pg" "$OUT/volumes" "$OUT/config"

# Databases that are disposable by construction — unit/e2e scratch DBs.
JUNK_DB_RE='(_unit_|_test$|^test|e2e|flatten_)'

echo "== Discovering Postgres containers =="
mapfile -t PG_CONTAINERS < <(
  docker ps --format '{{.Names}}|{{.Image}}' |
  awk -F'|' '$2 ~ /postgres|pgvector/ && $2 !~ /exporter/ { print $1 }'
)
[ "${#PG_CONTAINERS[@]}" -gt 0 ] || { echo "FATAL: no Postgres containers running"; exit 1; }
printf '  - %s\n' "${PG_CONTAINERS[@]}"

for c in "${PG_CONTAINERS[@]}"; do
  # The official image records its superuser here; fall back to the conventional default.
  user="$(docker exec "$c" printenv POSTGRES_USER 2>/dev/null || echo postgres)"
  echo "== $c (superuser: $user) =="

  # Roles and passwords live outside any single database.
  docker exec "$c" pg_dumpall -U "$user" --globals-only > "$OUT/pg/${c}__globals.sql"

  mapfile -t dbs < <(
    docker exec "$c" psql -U "$user" -d postgres -t -A -c \
      "select datname from pg_database where datallowconn and not datistemplate order by 1;" |
    tr -d '\r'
  )

  for db in "${dbs[@]}"; do
    [ -n "$db" ] || continue
    if [[ "$db" =~ $JUNK_DB_RE ]]; then echo "  skip $db (scratch)"; continue; fi
    # Dump to a file INSIDE the container, then docker cp it out. Redirecting a
    # binary -Fc stream through the host shell corrupts it on some platforms.
    docker exec "$c" pg_dump -U "$user" -Fc -f "/tmp/${db}.dump" "$db"
    docker cp "$c:/tmp/${db}.dump" "$OUT/pg/${c}__${db}.dump"
    docker exec "$c" rm -f "/tmp/${db}.dump"
    sz=$(du -m "$OUT/pg/${c}__${db}.dump" | cut -f1)
    echo "  dumped $db (${sz} MB)"
  done
done

echo "== Verifying every dump is readable =="
fail=0
for d in "$OUT"/pg/*.dump; do
  if pg_restore --list "$d" >/dev/null 2>&1 ||
     docker exec -i "${PG_CONTAINERS[0]}" pg_restore --list /dev/stdin < "$d" >/dev/null 2>&1; then
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
  docker ps -q |
  xargs -r docker inspect -f '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}' |
  grep -v '^$' | sort -u |
  grep -Ev '(postgres|pg-data|_db-data|db-data$)'
)
for vol in "${VOLS[@]}"; do
  # --warning=no-file-changed: live services mutate files mid-read. That is an
  # expected warning for observability data, not a failure — but we still let
  # real errors surface rather than swallowing all stderr.
  if docker run --rm -v "$vol:/v:ro" -v "$OUT/volumes:/b" alpine \
       tar czf "/b/$vol.tar.gz" --warning=no-file-changed -C /v . ; then
    echo "  - $vol ($(du -m "$OUT/volumes/$vol.tar.gz" | cut -f1) MB)"
  else
    echo "  ! $vol — tar reported an error, inspect before trusting this artifact"
  fi
done

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
