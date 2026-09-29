// A D1Database test double backed by node:sqlite (tests only; the Worker
// never imports this file).
//
// D1 is SQLite, so the Worker's SQL runs here exactly as written: the capped
// INSERT, the batched prune/select/count, the chunked ack. Only the thin
// binding API (prepare/bind/run/all/first, batch as one transaction) is
// re-implemented.
//
// Why not wrangler's getPlatformProxy, which gives a real local D1: it runs
// the workerd binary, which needs glibc >= 2.35, and the self-hosted CI
// runner's glibc is older. The job died with "GLIBC_2.32 not found" before a
// single test ran.

import { DatabaseSync } from 'node:sqlite';

type Row = Record<string, unknown>;

interface Result {
  success: true;
  results: Row[];
  meta: { changes: number };
}

const READS = /^\s*(SELECT|WITH|PRAGMA)\b/i;

class Statement {
  constructor(
    private readonly db: DatabaseSync,
    readonly sql: string,
    private readonly params: unknown[] = []
  ) {}

  bind(...params: unknown[]): Statement {
    return new Statement(this.db, this.sql, params);
  }

  execute(): Result {
    const stmt = this.db.prepare(this.sql);
    if (READS.test(this.sql)) {
      return {
        success: true,
        results: stmt.all(...this.params),
        meta: { changes: 0 },
      };
    }
    const info = stmt.run(...this.params);
    return {
      success: true,
      results: [],
      meta: { changes: Number(info.changes) },
    };
  }

  async run(): Promise<Result> {
    return this.execute();
  }

  async all(): Promise<Result> {
    return this.execute();
  }

  async first<T = Row>(): Promise<T | null> {
    return (this.execute().results[0] as T | undefined) ?? null;
  }
}

/** A fresh, empty in-memory database behind the D1 binding API. */
export function createD1(): D1Database {
  const db = new DatabaseSync(':memory:');
  const d1 = {
    prepare: (sql: string) => new Statement(db, sql),
    async batch(statements: Statement[]): Promise<Result[]> {
      // D1 runs a batch as a single transaction: all of it or none of it.
      db.exec('BEGIN');
      try {
        const results = statements.map((s) => s.execute());
        db.exec('COMMIT');
        return results;
      } catch (err) {
        db.exec('ROLLBACK');
        throw err;
      }
    },
    async exec(sql: string) {
      db.exec(sql);
      return { count: 1, duration: 0 };
    },
  };
  return d1 as unknown as D1Database;
}
