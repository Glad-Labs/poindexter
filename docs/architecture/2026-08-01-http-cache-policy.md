# HTTP cache policy: deny-by-default, opt-in caching

**Status:** accepted (2026-08-01)
**Applies to:** `src/cofounder_agent/middleware/cache_control.py` — every response the worker serves
**Supersedes:** the original allowlist policy (mutations `no-store`, a few prefixes cached, everything else `private, max-age=60`)

## Decision

`CacheControlMiddleware` is **deny-by-default**. Any response it is not
explicitly told to cache comes back `no-store`. Every response that _is_
storable also carries `Vary: Authorization`.

A route becomes cacheable one of two ways:

1. **It sets its own `Cache-Control`.** The middleware never overwrites one
   that is already on the response. This is the seam the operator console
   mount uses (`utils/operator_console.py` serves its unhashed assets
   `no-cache`).
2. **It matches a prefix in one of two short allowlists** in the middleware:
   `_STATIC_PREFIXES` (catalogs that change on deploy, not on operator action)
   and `_REVALIDATE_PREFIXES` (file routes, which carry an ETag).

Route 1 covers more than it looks like. `/.well-known/*` is cached at
`public, max-age=3600` by the OAuth SDK's own header, and the podcast/video
episode routes at `public, max-age=86400` by theirs — none of them appear in an
allowlist, and adding them would be inert. When auditing what is cached, read
the responses, not just this middleware.

### Position in the stack (closed the original known boundary)

`CacheControlMiddleware` is registered **outermost-but-one**: only the
Prometheus RED timer wraps it, and that middleware is pure observation — it can
never mint a response, so "every response is stamped" still holds while RED
latency keeps timing the full request.

As first shipped, the middleware sat _innermost_, so any middleware that
produced its own response bypassed it entirely: `TokenValidation` 401s,
`InputValidation`/`PayloadInspection` 400s, and CORS preflights all returned
with **no `Cache-Control` at all** and fell back to browser heuristic caching.
The 2026-08-02 reorder closed that: those responses now carry `no-store`,
verified by composed-stack tests that trigger each short-circuit for real
(`tests/unit/utils/test_middleware_config.py`) — the same probes returned no
header at all under the old order.

Preflights deserve a note: stamping `no-store` on an `OPTIONS` response does
**not** defeat preflight caching. The browser's CORS-preflight cache is a
separate cache governed by `Access-Control-Max-Age` (Fetch spec), not by
`Cache-Control`, so the preflight response correctly carries both
(`no-store` + `max-age=600` for the preflight cache).

Ordering rules, should the stack grow:

- A middleware that **can produce a response** must be registered before
  (inside) `CacheControlMiddleware`.
- A middleware that **must observe the whole request** (timers) may sit
  outside it only if it never responds.

The one remaining gap is unhandled-exception **500s**: they propagate as
exceptions through `dispatch` — there is no response object to stamp — and the
500 is built by Starlette's `ServerErrorMiddleware`, which no user middleware
can wrap. RFC 9111 §4.2.2 does not make 500s heuristically cacheable, so this
is acceptable and not worth chasing.

(Deploying the reorder surfaced one real instance of that gap: OTel's FastAPI
span-namer crashed on any partial-match-only request — CORS preflights — and
500'd it before the middleware stack ran at all. Fixed separately by the shim
in `services/telemetry.py::_patch_otel_fastapi_partial_match_crash`; the live
preflight check below is the regression probe for both changes.)

## Why

The previous policy inverted these defaults, and the "safe default" for
unclassified paths was `private, max-age=60`. An audit on 2026-08-01 found
**68 of 85 GET-able routes reaching that catch-all** — the fallthrough was the
primary path, not the exception, and every new route silently inherited
caching.

Two concrete failures, both measured in a real browser via the Resource Timing
API (`transferSize === 0` proves the response came from cache with no network
request), not inferred from the spec:

| Surface                       | Old directive                       | Poll interval | Effect                                                                        |
| ----------------------------- | ----------------------------------- | ------------- | ----------------------------------------------------------------------------- |
| `/api/activity`               | `private, max-age=60` (fallthrough) | 3s            | most polls answered from cache; "live" panel showed minute-old data           |
| `/api/trace/{id}`             | fallthrough                         | 6s            | same                                                                          |
| `/api/gpu/queue`, `/api/logs` | fallthrough                         | 10s           | same                                                                          |
| `/health`, `/api/health`      | `public, max-age=300` (explicit)    | 30s           | **a dead worker still read `200 healthy` in the console for up to 5 minutes** |

The health case is the sharpest: while a cache entry is fresh the browser
issues _no request at all_, so the console's liveness signal could not observe
the thing it reports on. It was also the one case the inversion alone would not
have fixed, because health was an explicit opt-in rather than a fallthrough —
it had to be removed from the allowlist deliberately.

Two assumptions worth discarding, both tested rather than argued:

- **An `Authorization` header does not stop caching.** RFC 9111 §3.5 restricts
  _shared_ caches; a browser's private cache is free to store and reuse an
  authenticated response, and Chrome does.
- **The console's staleness indicator cannot catch this.** `computeStale` keys
  off `lastUpdatedAt`, which `usePolledResource` sets on every _successful_
  fetch — cache hits included. It reports fresh while showing stale data.

The API surface gives up nothing by not being cached. Its consumers — the
operator console, the MCP servers, the CLI — each drive their own request
cadence, so HTTP caching never reduced load on the worker. It only decided how
stale an answer was allowed to be.

## `Vary: Authorization`

Without it a browser keys its cache on URL alone, so two tokens hitting the
same URL inside a freshness window can be served each other's body. With a
single operator that is benign; under the multi-tenant SaaS direction it is a
cross-tenant leak.

Two implementation details that are easy to get wrong:

- It is applied based on the **effective** `Cache-Control`, including one a
  route set for itself. A route opting into a long `max-age` (the podcast and
  video episode routes use `public, max-age=86400`) is exactly where a missing
  `Vary` would hurt most.
- It **merges** rather than assigns. `CORSMiddleware` runs _inside_
  `CacheControlMiddleware` (since the 2026-08-02 reorder), so its
  `Vary: Origin` is already on the response when the merge happens; assigning
  would drop it. `Vary: *` already means "never reuse" and is left alone.

`no-store` responses get no `Vary` — nothing stores them, so it would be dead
weight on the overwhelming majority of responses.

## Consequences

**Adding a route requires no cache decision.** That is the point: forgetting
now yields a route that is merely uncached, never one that is quietly wrong.

**Adding a route to an allowlist requires justifying it.** The bar for
`_STATIC_PREFIXES` is "this changes when the deploy changes, not when the
operator acts". The bar for `_REVALIDATE_PREFIXES` is "the response carries a
validator" — Starlette emits an ETag for `FileResponse` but **not** for
`JSONResponse`, so `no-cache` on a JSON route buys a round trip that can never
answer `304`. Prefer `no-store` there.

**`no-cache` and `no-store` are different and the difference matters.**
`no-store` forbids keeping the response at all. `no-cache` keeps it and
revalidates before every use, which is only useful with a validator. The
console mount wants `no-cache` (33 unhashed assets, all with ETags, revalidated
into bodiless 304s); a JSON API route wants `no-store`.

## Verifying

```bash
curl -sS -D - -o /dev/null http://localhost:8002/api/activity | grep -iE 'cache-control|vary'
```

To prove a browser is actually revalidating rather than reading its own cache,
check `transferSize` from the Resource Timing API — a `0` on a `200` means the
network was never touched:

```js
await fetch(url);
const e = performance.getEntriesByName(url).pop();
console.log(e.transferSize, e.encodedBodySize); // 0 + non-zero body => served from cache
```

Pinned by `tests/unit/middleware/test_cache_control.py` and
`tests/unit/utils/test_operator_console.py`.

## Related

- `src/cofounder_agent/console/README.md` §1 — the console's own caching story, and the deploy bug that started this
- `utils/operator_console.py` — the mount that sets its own `no-cache`
