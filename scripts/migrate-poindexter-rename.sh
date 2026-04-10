#!/usr/bin/env bash
#
# One-time migration: rename gladlabs_brain → poindexter_brain and the
# gladlabs role → poindexter on an existing local Postgres deployment.
#
# Run this AFTER pulling the rebrand commit and BEFORE the next worker
# restart. The script:
#
#   1. Stops every container that holds an open connection to postgres-local
#      (worker, brain-daemon, grafana, pgadmin, gitea, woodpecker, woodpecker-agent).
#   2. Brings postgres-local up by itself.
#   3. Issues two ALTER statements over psql (database rename + role rename).
#   4. Stops postgres-local cleanly.
#   5. Reminds you to update .env.local before bringing the stack back up.
#
# This is a destructive-feeling but technically reversible operation —
# postgres just renames the catalog entries, no data is moved or rewritten.
#
# Usage:
#   bash scripts/migrate-poindexter-rename.sh
#
# If something goes wrong you can rename them back the same way:
#   ALTER DATABASE poindexter_brain RENAME TO gladlabs_brain;
#   ALTER USER poindexter RENAME TO gladlabs;

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# Default to the legacy container name (gladlabs-postgres-local) since this
# migration script runs BEFORE the container has been renamed by Stage 2b.
# Override via POSTGRES_CONTAINER env var if your container has a different name.
CONTAINER="${POSTGRES_CONTAINER:-gladlabs-postgres-local}"
OLD_DB="${OLD_DB:-gladlabs_brain}"
NEW_DB="${NEW_DB:-poindexter_brain}"
OLD_USER="${OLD_USER:-gladlabs}"
NEW_USER="${NEW_USER:-poindexter}"

info "Migration plan:"
info "  container: $CONTAINER"
info "  database:  $OLD_DB → $NEW_DB"
info "  user:      $OLD_USER → $NEW_USER"
echo

# 1. Stop everything that talks to the database
info "Stopping containers that hold open connections..."
docker compose -f docker-compose.local.yml stop \
  worker brain-daemon grafana pgadmin gitea woodpecker woodpecker-agent 2>/dev/null || true
ok "Dependent containers stopped"

# 2. Bring postgres-local up
info "Starting postgres-local..."
docker compose -f docker-compose.local.yml up -d postgres-local
sleep 3

# 3. Verify it's reachable as the OLD user/db
if ! docker exec "$CONTAINER" pg_isready -U "$OLD_USER" -d "$OLD_DB" >/dev/null 2>&1; then
    if docker exec "$CONTAINER" pg_isready -U "$NEW_USER" -d "$NEW_DB" >/dev/null 2>&1; then
        warn "Looks like the rename has already been applied — found $NEW_USER/$NEW_DB."
        warn "Skipping ALTER statements."
        ALREADY_MIGRATED=true
    else
        fail "Cannot reach postgres as either $OLD_USER/$OLD_DB or $NEW_USER/$NEW_DB. Check the container."
    fi
else
    ALREADY_MIGRATED=false
fi

if [ "$ALREADY_MIGRATED" = "false" ]; then
    info "Renaming database and role over psql (connecting via 'postgres' maintenance DB)..."
    # Connect to the 'postgres' maintenance DB so we don't hold a connection to
    # the database we're about to rename.
    docker exec "$CONTAINER" psql -U "$OLD_USER" -d postgres -v ON_ERROR_STOP=1 -c \
        "ALTER DATABASE \"$OLD_DB\" RENAME TO \"$NEW_DB\";"
    ok "Database renamed: $OLD_DB → $NEW_DB"

    docker exec "$CONTAINER" psql -U "$OLD_USER" -d postgres -v ON_ERROR_STOP=1 -c \
        "ALTER USER \"$OLD_USER\" RENAME TO \"$NEW_USER\";"
    ok "Role renamed: $OLD_USER → $NEW_USER"

    # Sanity check
    if docker exec "$CONTAINER" pg_isready -U "$NEW_USER" -d "$NEW_DB" >/dev/null 2>&1; then
        ok "Verified: postgres reachable as $NEW_USER/$NEW_DB"
    else
        fail "Post-rename verification failed. Both $OLD_USER and $NEW_USER appear unreachable."
    fi
fi

# 4. Stop postgres-local so the next docker compose up reads the new env
info "Stopping postgres-local..."
docker compose -f docker-compose.local.yml stop postgres-local
ok "Postgres stopped"

echo
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Database rename complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo
echo "Next steps (manual):"
echo
echo "  1. Update .env.local — set these to the new values:"
echo "       LOCAL_POSTGRES_USER=poindexter"
echo "       LOCAL_POSTGRES_DB=poindexter_brain"
echo "     (LOCAL_POSTGRES_PASSWORD stays the same — the role keeps its password)"
echo
echo "  2. Move the data root on the host (one-time):"
echo "       mv ~/.gladlabs ~/.poindexter"
echo "     (This carries over your existing podcast/video/image files.)"
echo
echo "  3. Bring the full stack back up:"
echo "       docker compose -f docker-compose.local.yml up -d"
echo
echo "  4. Tail the worker logs and confirm it connected:"
echo "       docker logs -f poindexter-worker"
echo
