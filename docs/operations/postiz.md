# Postiz: operating and upgrading the social hub

Postiz is the self-hosted social distribution hub behind the opt-in `postiz`
compose profile. Poindexter talks to it only through its public API
(`services/integrations/postiz_client.py`), and three things depend on that
API answering. Approving a social draft calls `POST /public/v1/posts`.
`SyncPostizDeliveryStateJob` and the brain's `postiz_queue_watch` probe both
read `GET /public/v1/posts`.

The profile runs four containers:

| Container                    | Role                                                                                                                                                  |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `poindexter-postiz`          | The app. pm2 runs three processes inside it: `backend` (the API, port 3000), `frontend` and `orchestrator` (Temporal workers that do the publishing). |
| `poindexter-postiz-db`       | Postgres for Postiz, plus Temporal's `temporal` and `temporal_visibility` databases.                                                                  |
| `poindexter-postiz-redis`    | Cache and queues.                                                                                                                                     |
| `poindexter-postiz-temporal` | Temporal server (`temporalio/auto-setup`), with SQL visibility on `postiz-db`.                                                                        |

Both images are pinned, never `:latest`, and
`tests/unit/services/test_postiz_compose_pins.py` enforces that. The reasons are
below.

## Diagnosing "API unreachable"

When the brain reports `Postiz queue wedged … API unreachable`, first find out
whether the container is hung or the backend cannot boot. They need different
fixes, and a restart only fixes a hang.

```bash
docker inspect poindexter-postiz --format '{{.State.Health.Status}}'
docker exec poindexter-postiz pm2 jlist | python3 -c 'import json,sys; [print(p["name"], p["pm2_env"]["status"], "restarts:", p["pm2_env"]["restart_time"]) for p in json.load(sys.stdin)]'
docker exec poindexter-postiz tail -n 80 /root/.pm2/logs/backend-error.log
```

pm2 restarts a crashed `backend` inside the container, so Docker's
`RestartCount` stays at 0 and the brain's container restart-loop watch cannot
see the loop. The pm2 restart count and `backend-error.log` can. `docker logs`
interleaves the orchestrator's very chatty output with everything else, so read
the pm2 log directly.

## Failure: `tables can have at most 1600 columns`

### Symptom

The backend exits on every boot with
`MASTRA_STORAGE_PG_ALTER_TABLE_FAILED` / `tables can have at most 1600 columns`
(SQLSTATE `54011`), naming `mastra_ai_spans`. The frontend and orchestrator
start normally, the container stays up but reports unhealthy, and the API does
not answer. Restarting changes nothing.

### Cause

The container entrypoint runs `prisma db push --accept-data-loss` against
Postiz's Prisma schema before starting the app. The backend then initialises
its Mastra storage (Postiz's AI-agent layer), which adds any column its own
schema has and the table lacks.

Up to postiz-app **v2.23.0**, the two schemas disagreed. Prisma's
`mastra_ai_spans` model had 21 columns, while the bundled Mastra (`@mastra/core`
1.21.0) wanted 43. Every backend boot added 22 columns, and every container
start dropped them again. `mastra_scorers` churned one column the same way.

PostgreSQL keeps a dropped column in `pg_attribute` for good, and dropped
columns count toward the 1600-column limit
([Appendix K](https://www.postgresql.org/docs/16/limits.html)). `VACUUM FULL`
does not give the slots back. So every restart spent 22 of the table's 1600
slots. After about 72 restarts the next `ADD COLUMN` failed and the backend
could not boot. Each restart before that, including restarts made to "heal" the
container, brought the failure closer.

v2.24.0 is the first release whose Prisma models match its Mastra schemas, and
it is the floor that the pin test enforces. First seen 2026-09-28
(Glad-Labs/poindexter#1091): 21 live columns and 1579 dropped ones.

### Check the headroom

Run against `postiz-db`. A healthy table has a `dropped` count that stays the
same across restarts.

```sql
SELECT c.relname,
       count(*) FILTER (WHERE NOT a.attisdropped) AS live,
       count(*) FILTER (WHERE a.attisdropped)     AS dropped,
       max(a.attnum)                               AS slots_used  -- limit 1600
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0
GROUP BY c.relname
HAVING count(*) FILTER (WHERE a.attisdropped) > 0
ORDER BY slots_used DESC;
```

```bash
docker exec -i poindexter-postiz-db psql -U postiz -d postiz < headroom.sql
```

### Recover

Recreating the table is the only way to get the slots back. Back up first:

```bash
docker exec poindexter-postiz-db pg_dump -U postiz -d postiz -Fc > postiz-$(date -u +%Y%m%dT%H%M%SZ).dump
```

`mastra_ai_spans` holds Mastra's AI-agent traces and stays empty unless
Postiz's AI features are in use. **If it is empty**, drop it and restart.
`prisma db push` and Mastra recreate it with every slot free:

```bash
docker exec poindexter-postiz-db psql -U postiz -d postiz -c "SELECT count(*) FROM mastra_ai_spans"
docker exec poindexter-postiz-db psql -U postiz -d postiz -c "SET lock_timeout = '10s'; DROP TABLE mastra_ai_spans"
docker restart poindexter-postiz
```

**If it has rows**, copy the table into a fresh one instead. The copy gets new
attribute numbers for the live columns only:

```sql
BEGIN;
ALTER TABLE mastra_ai_spans RENAME TO mastra_ai_spans_old;
CREATE TABLE mastra_ai_spans (LIKE mastra_ai_spans_old INCLUDING ALL);
INSERT INTO mastra_ai_spans SELECT * FROM mastra_ai_spans_old;
DROP TABLE mastra_ai_spans_old;
COMMIT;
```

`INCLUDING ALL` recreates the indexes under generated names. The next
`prisma db push` restores the names Prisma expects. On a version below v2.24.0
the leak resumes after either recovery. Upgrade as described next.

## Upgrading Postiz

1. Read the release's upgrade notes for new required environment variables and
   Temporal workflow changes.
2. Check the new image's schemas. The check must print `OK` and exit 0:

   ```bash
   python scripts/postiz_upgrade_check.py ghcr.io/gitroomhq/postiz-app:<tag>
   ```

   It reads Mastra's table schemas and Postiz's Prisma models out of the image
   and lists every column the two disagree on. That is the churn that spends
   attribute slots on every restart. It exits 1 on drift and 2 when it could not
   compare anything, for example when the image layout moved. Exit 2 is not a
   pass. For comparison, v2.21.10 fails with 22 columns on `mastra_ai_spans` and
   one on `mastra_scorers`. v2.24.0 passes across 43 tables.

3. Back up `postiz-db` (command above).
4. For a large jump, replay the upgrade in a sandbox before touching the live
   stack:
   - Restore the dump into a throwaway `postgres:16-alpine` on a network created
     with `docker network create --internal`, so nothing inside it can reach a
     social platform.
   - Scrub the OAuth tokens in the copy. Using a copied refresh token can revoke
     the live one:
     `UPDATE "Integration" SET token = 'scrubbed', "refreshToken" = 'scrubbed';`
   - Start the new image against it, with a throwaway Redis and Temporal (see the
     Temporal section below).
   - Confirm the backend answers. Replay `PostizClient`'s calls with the org API
     key from the copy's `Organization."apiKey"`: `GET /public/v1/posts`,
     `GET /public/v1/integrations`, and a `POST /public/v1/posts` (it will end in
     `ERROR` because there is no network). Restart the container twice and
     re-run the headroom query. `dropped` must not move.
5. Move the tag in **both** `docker-compose.local.yml` and
   `docker-compose.consumer.yml`. The pin test requires both to match, because
   they share the `gladlabs-postiz-*` volumes. Merge. The deploy recreates the
   container, and the new image's `prisma db push` migrates the database on
   start.
6. Verify on the live stack: the container is healthy; the next brain heartbeat
   shows `postiz_queue_watch` as `ok`
   (`audit_log`, `event_type = 'brain.cycle_heartbeat'`,
   `details->'probe_status'->>'postiz_queue_watch'`); and the headroom query
   shows the same `dropped` count before and after one restart.

To roll back, pin the previous tag and restore the dump if the schema migration
has to be undone. Rolling back below v2.24.0 brings the column leak back.

## Temporal: search attributes on a fresh namespace

At boot the Postiz backend registers two custom search attributes of type Text,
`organizationId` and `postId`, if they are missing. SQL visibility (our
`postgres12` setup) allows a namespace only **3** Text attributes. Upstream's own
compose runs Elasticsearch visibility, which has no such limit.

When `temporalio/auto-setup` creates a new namespace, it also registers demo
attributes, two of them Text (`CustomStringField`, `CustomTextField`). That
leaves one Text slot, so Postiz's registration fails and the backend dies on
boot:

```
Error: 3 INVALID_ARGUMENT: Unable to create search attributes: cannot have more than 3 search attribute of type Text.
```

`postiz-temporal` sets `SKIP_ADD_CUSTOM_SEARCH_ATTRIBUTES=true` so that new
namespaces never get the demo attributes. A namespace that already has them can
be repaired in place:

```bash
docker exec poindexter-postiz-temporal sh -c 'temporal operator search-attribute remove --address $(hostname -i):7233 --name CustomStringField --name CustomTextField --yes'
docker restart poindexter-postiz
```

Check the result with
`temporal operator search-attribute list --address $(hostname -i):7233` inside
`poindexter-postiz-temporal`. `organizationId` and `postId` should appear as
Text.
