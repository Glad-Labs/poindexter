// Types for test-only imports. The Worker's tsconfig loads only
// @cloudflare/workers-types, so declare exactly what the tests use rather
// than pulling in @types/node (whose globals collide with the Workers ones).

// node:sqlite backs the D1 test double (src/test-d1.ts).
declare module 'node:sqlite' {
  export class StatementSync {
    run(...params: unknown[]): {
      changes: number | bigint;
      lastInsertRowid: number | bigint;
    };
    all(...params: unknown[]): Record<string, unknown>[];
  }
  export class DatabaseSync {
    constructor(path: string);
    exec(sql: string): void;
    prepare(sql: string): StatementSync;
    close(): void;
  }
}

// Vite's `?raw` suffix imports a file's text (the tests read wrangler.toml).
declare module '*?raw' {
  const content: string;
  export default content;
}
