#!/usr/bin/env python3
"""Pre-upgrade check: do a Postiz image's Prisma models match its Mastra schemas?

Run this before moving the ``postiz`` image tag in the compose files.

Why it exists (Glad-Labs/poindexter#1091): the postiz-app entrypoint runs
``prisma db push --accept-data-loss`` before the app starts, and the Mastra
storage bundled with the app re-adds, at every backend boot, the columns its
own schema has and the table lacks. A column that Mastra wants but Postiz's
Prisma model leaves out is therefore dropped and re-added on every container
start. PostgreSQL never reuses a dropped column's attribute number, and dropped
columns count toward its 1600-column limit, so each such column spends one
slot per restart until the table cannot take another column and the backend
no longer boots. postiz-app v2.21.10 churned 22 columns of ``mastra_ai_spans``
per start; v2.24.0 churns none.

The check reads both schemas out of the image with the image's own node, so it
sees exactly what the container will run. It is static: a column it flags is a
column the two schemas disagree on. Whether Mastra actually re-adds that column
at boot depends on which of its migrations touch the table, and the sandbox
replay in docs/operations/postiz.md is the ground truth when it matters.

Usage:
    python scripts/postiz_upgrade_check.py ghcr.io/gitroomhq/postiz-app:v2.25.0

Exit status: 0 when no modeled table disagrees, 1 when any does, 2 when the
check could not run or found nothing to compare (a check that compared no
tables has not passed). Needs Docker; pulls the image if it is not present.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

# Paths inside the postiz-app image. If upstream moves either one, the probe
# exits non-zero and this check reports "could not run" rather than passing.
MASTRA_STORAGE_MODULE = "/app/node_modules/@mastra/core/dist/storage/index.cjs"
PRISMA_SCHEMA = "/app/libraries/nestjs-libraries/src/database/prisma/schema.prisma"

# Runs under the image's node. Prints one JSON object per Mastra table.
# Mastra gives every `timestamp` column a `<name>Z` TIMESTAMPTZ companion, so
# those count as wanted columns too. Relation fields in a Prisma model are not
# columns and are skipped, and a model's `@@map("...")` names its table.
_PROBE_JS = r"""
const fs = require('fs');
const storage = require(process.argv[1]);
const prisma = fs.readFileSync(process.argv[2], 'utf8');
const bodies = [...prisma.matchAll(/^model (\w+) \{([\s\S]*?)^\}/gm)];
const modelNames = new Set(bodies.map((m) => m[1]));
const tables = {};
for (const [, name, body] of bodies) {
  const mappedTable = body.match(/@@map\("([^"]+)"\)/);
  const cols = [];
  for (const line of body.split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('@@') || t.startsWith('//')) continue;
    const [field, type = ''] = t.split(/\s+/);
    if (modelNames.has(type.replace(/[?\[\]]/g, ''))) continue;
    const mappedCol = t.match(/@map\("([^"]+)"\)/);
    cols.push(mappedCol ? mappedCol[1] : field);
  }
  tables[mappedTable ? mappedTable[1] : name] = cols;
}
const schemas = storage.TABLE_SCHEMAS;
if (!schemas || typeof schemas !== 'object') {
  console.error('TABLE_SCHEMAS not found in ' + process.argv[1]);
  process.exit(3);
}
for (const [table, schema] of Object.entries(schemas)) {
  const want = new Set(Object.keys(schema));
  for (const [col, def] of Object.entries(schema)) {
    if (def && def.type === 'timestamp') want.add(col + 'Z');
  }
  const have = tables[table];
  console.log(JSON.stringify({
    table,
    modeled: Boolean(have),
    mastra: want.size,
    prisma: have ? have.length : 0,
    churn: have ? [...want].filter((c) => !have.includes(c)) : [],
    prisma_only: have ? have.filter((c) => !want.has(c)) : [],
  }));
}
"""


@dataclass
class TableDiff:
    table: str
    modeled: bool
    mastra: int
    prisma: int
    churn: list[str] = field(default_factory=list)
    prisma_only: list[str] = field(default_factory=list)


class CheckError(Exception):
    """The check could not produce a verdict."""


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=900)


def probe_command(image: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull",
        "missing",
        "--network",
        "none",
        "--entrypoint",
        "node",
        image,
        "-e",
        _PROBE_JS,
        MASTRA_STORAGE_MODULE,
        PRISMA_SCHEMA,
    ]


def parse_probe_output(stdout: str) -> list[TableDiff]:
    rows: list[TableDiff] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            rows.append(
                TableDiff(
                    table=str(raw["table"]),
                    modeled=bool(raw["modeled"]),
                    mastra=int(raw["mastra"]),
                    prisma=int(raw["prisma"]),
                    churn=[str(c) for c in raw.get("churn", [])],
                    prisma_only=[str(c) for c in raw.get("prisma_only", [])],
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise CheckError(f"unparseable probe output line {line[:120]!r}: {exc}") from exc
    return rows


def verdict(rows: Sequence[TableDiff]) -> int:
    """0 = no modeled table disagrees, 1 = at least one does.

    Raises CheckError when nothing was compared, so an image whose layout
    moved can never read as a pass.
    """
    modeled = [r for r in rows if r.modeled]
    if not modeled:
        raise CheckError("compared 0 tables: no Mastra table has a Prisma model in this image")
    return 1 if any(r.churn for r in modeled) else 0


def check(image: str, runner: Runner = _run) -> tuple[int, list[TableDiff]]:
    try:
        proc = runner(probe_command(image))
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckError(f"could not run docker: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise CheckError(f"probe exited {proc.returncode}: {detail}")
    rows = parse_probe_output(proc.stdout)
    return verdict(rows), rows


def render(image: str, rows: Sequence[TableDiff]) -> str:
    modeled = [r for r in rows if r.modeled]
    drifting = [r for r in modeled if r.churn]
    lines = [f"{image}: compared {len(modeled)} Mastra table(s) against Prisma models"]
    for r in drifting:
        lines.append(
            f"  DRIFT {r.table}: {len(r.churn)} column(s) Mastra wants and Prisma "
            f"drops on every container start: {', '.join(r.churn)}"
        )
    unmodeled = [r.table for r in rows if not r.modeled]
    if unmodeled:
        lines.append(
            f"  note: {len(unmodeled)} Mastra table(s) have no Prisma model "
            f"(outside this check): {', '.join(unmodeled)}"
        )
    lines.append(
        "  FAIL: this image would spend attribute slots on every restart"
        if drifting
        else "  OK: no column churn between Prisma and Mastra"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, runner: Runner = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "image", help="postiz-app image reference, e.g. ghcr.io/gitroomhq/postiz-app:v2.25.0"
    )
    args = parser.parse_args(argv)
    try:
        code, rows = check(args.image, runner)
    except CheckError as exc:
        print(f"{args.image}: check could not run: {exc}", file=sys.stderr)
        return 2
    print(render(args.image, rows))
    return code


if __name__ == "__main__":
    sys.exit(main())
