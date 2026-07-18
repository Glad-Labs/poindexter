#!/usr/bin/env bash
# backup-precious.sh — pre-migration backup of the precious + useful tiers.
# Run from Git Bash on the CURRENT box while the Docker-Desktop stack is up.
# Usage: bash scripts/linux/backup-precious.sh /d/migration-backup   (drive-2 mount)
set -euo pipefail
DEST="${1:?usage: backup-precious.sh <dest-dir>}"
PG=poindexter-postgres-local
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/poindexter-backup-$STAMP"
mkdir -p "$OUT/pg" "$OUT/volumes" "$OUT/config"

echo "== Postgres globals (roles/passwords) =="
docker exec "$PG" pg_dumpall -U poindexter --globals-only > "$OUT/pg/globals.sql"

echo "== Precious DBs (skip junk *_unit_/test/e2e/flatten_*) =="
for db in poindexter_brain prefect langfuse; do
  echo "  - $db"
  docker exec "$PG" pg_dump -U poindexter -Fc "$db" > "$OUT/pg/$db.dump"
done
# Postiz DB (separate container/volume — adjust name if different)
docker exec "$PG" pg_dump -U poindexter -Fc postiz > "$OUT/pg/postiz.dump" 2>/dev/null || \
  echo "  (postiz DB not on this PG instance — check the Postiz compose service)"

echo "== Useful volumes (best-effort tar) =="
for vol in gladlabs-grafana-data gladlabs-glitchtip-db gladlabs-langfuse-clickhouse \
           gladlabs-langfuse-minio gladlabs-uptime-kuma gladlabs-postiz-uploads; do
  docker run --rm -v "$vol:/v:ro" -v "$OUT/volumes:/b" alpine \
    tar czf "/b/$vol.tar.gz" -C /v . 2>/dev/null && echo "  - $vol" || echo "  (skip $vol)"
done

echo "== Operator config =="
cp -r "$HOME/.poindexter" "$OUT/config/dot-poindexter"      # bootstrap.toml + secrets + logs
cp -r "$HOME/.claude"     "$OUT/config/dot-claude"          # memory, plugins, settings, keybindings
echo "$OUT" > "$DEST/LATEST-BACKUP.txt"
echo "DONE -> $OUT"
