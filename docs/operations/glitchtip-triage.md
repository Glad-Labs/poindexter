# GlitchTip noise control

GlitchTip (http://localhost:8080, org `glad-labs`, project `poindexter`) is the
runtime-error sink for worker / brain / voice. Left alone it accumulates
hundreds of "issues" that are mostly not bugs, which is worse than having no
error tracker: a 364-issue list trains you to ignore the list.

Noise is suppressed at **two** layers. Reach for the earlier one first — it is
strictly better to never capture a non-error than to capture and then close it.

| Layer            | Where                                                                                     | Use it for                                              |
| ---------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| **Capture-side** | `services/sentry_integration.py::_before_send`                                            | Events that should never have been errors at all        |
| **Triage-side**  | `brain/glitchtip_triage_probe.py` + `app_settings.glitchtip_triage_auto_resolve_patterns` | Real errors that are known/expected, and stale one-offs |

## Layer 1 — capture-side (`_before_send`)

Runs in-process before an event is sent. Two knobs, both `app_settings`-driven
so they change without a redeploy (read through the cached `SiteConfig`, so the
1-minute `reload_site_config` job propagates edits):

- **`sentry_drop_exception_types`** — CSV of exception class names to drop
  entirely. Matched by name across the MRO, so it needs no import of the
  raising package and covers subclasses.
- **`sentry_fingerprint_scrub_patterns`** — JSON array of
  `[regex, replacement]` pairs applied to the event's **grouping fingerprint
  only**. The message an operator reads is never modified.

### When to drop an exception type

Only when the exception is _expected control flow_ — something the code raises
to steer itself, which the SDK misreads as an unhandled failure. Current
entries:

- `GraphInterrupt` — LangGraph `interrupt()` suspending a graph for operator
  approval. A pause, not a failure.
- `GpuBusyError` — `gpu.lock(..., max_wait_s=...)` refusing a hopeless wait so
  fail-soft callers skip cleanly. Already recorded as an `info`-severity
  `gpu_admission_rejected` finding, so capturing it as an _error_ double-reports
  one designed outcome at two contradictory severities.

A real failure that merely happens often does **not** belong here — that is
Layer 2's job, where closing an issue still leaves a record and a recurrence
re-opens it.

### Why fingerprints need scrubbing

GlitchTip derives an issue's identity from the exception value (or log
message). Any volatile token in that text mints a brand-new issue **per
event**, which looks like a widespread outage and buries everything else.

Only set a scrub pattern for text that is genuinely meaningless for grouping.
The default three, each from a real incident:

| Pattern                 | Real signature                                                   | Damage                        |
| ----------------------- | ---------------------------------------------------------------- | ----------------------------- |
| `/tmp/tmp[A-Za-z0-9_]+` | `S3UploadFailedError: Failed to upload /tmp/tmpnjvtpvv5.json …`  | 164 issues from ONE R2 outage |
| UUID                    | `Request validation middleware error for /api/chat/watch/<uuid>` | 11 issues                     |
| `\d+\.\d+s\b`           | `pg_advisory_lock wait exceeded 44.999968992000504s`             | 31 issues                     |

The fingerprint is only overridden when scrubbing actually changed the text, so
events with no volatile tokens keep the SDK's default grouping. Bare integers
are deliberately **not** scrubbed — they are usually meaningful (HTTP status,
errno), and full-precision floats were the actual fragmenter.

A malformed setting logs loud and falls back to the built-in defaults rather
than silently disabling scrubbing.

## Layer 2 — the triage probe ruleset

`glitchtip_triage_auto_resolve_patterns` is a JSONB array evaluated **in
declaration order**; `_match_rule` returns the **first** match. Each entry:

```json
{
  "title_pattern": "<regex>",
  "action": "resolve" | "ignore",
  "reason": "<why this is known noise>",
  "max_count": 1000,
  "min_age_days": 7,
  "level_in": ["error"]
}
```

- **`resolve`** closes the issue. Preferred for backlog hygiene — the list
  stays short, and a recurrence re-opens it as a GlitchTip regression, so
  nothing is lost.
- **`ignore`** suppresses alerting but leaves the issue open forever. Use only
  for mechanical echoes that need no closure (e.g. `^\[operator_notifier\]`,
  which is the notifier logging its own outbound page). `ignore` rules are
  exempt from the ceiling described below.

### Trap: the silent `max_count` ceiling

**A `resolve` rule with no explicit `max_count` is silently bounded to
`glitchtip_triage_default_resolve_max_count` (50).** That is the #304
runaway-outage backstop: if an issue is exploding, stop auto-closing it and let
a human look. But `count` is **cumulative and never resets**, so a chronic
known-noise issue eventually crosses any ceiling, the rule stops matching
_permanently_, and the noise it exists to suppress starts paging again.

This trap has now re-armed three times (2026-07-12, 2026-07-14, 2026-08-08). It
is pinned by
`tests/unit/services/migrations/test_glitchtip_triage_patterns_seed.py`, which
fails if **any** seeded `resolve` rule omits `max_count`.

Size ceilings from the issue's **observed accumulation rate**, not a round
number — roughly a year of headroom at current rate. That still trips within
days on a genuine 10× spike:

```
count / (lastSeen - firstSeen in days) = events/day
max_count ≈ count + 365 × events/day
```

Watch for burst issues (many events in an hour, then dormant): their rate is
meaningless to extrapolate — cap those at a multiple of the current count.

### The catch-all GC rule

The last entry is a `resolve` rule with `title_pattern: "."`, gated on
`min_age_days: 7` **and** `max_count: 5` — it closes stale one-off transients
that no specific rule covers. Before it existed, 272 of 364 open issues were
exactly that, and `min_age_days` had never been used once despite being built
for it.

**It must stay last.** A catch-all anywhere else shadows every rule declared
after it. This ordering is enforced by a contract test, not just a comment.

Note `min_age_days` gates on **`firstSeen`**, not `lastSeen` — it means "this
issue has existed for N days", not "has been quiet for N days". Combined with a
low `max_count`, that is the intended "old and rare" filter.

## Diagnostics

The probe's own helpers are the fastest way to reason about live state, run
inside the brain container so they reuse its DSN and secret decryption:

```bash
docker exec poindexter-brain-daemon python3 -c "
import asyncio,sys; sys.path.insert(0,'/app/brain')
import asyncpg, bootstrap, httpx, glitchtip_triage_probe as p
async def m():
    pool=await asyncpg.create_pool(bootstrap.resolve_database_url())
    tok=await p._read_secret(pool,p.TOKEN_SETTING_KEY)
    base=await p._read_setting(pool,p.BASE_URL_SETTING_KEY,p.BASE_URL_DEFAULT)
    org=await p._read_setting(pool,p.ORG_SLUG_SETTING_KEY,p.ORG_SLUG_DEFAULT)
    rules=await p._read_rules(pool, await p._read_default_resolve_max_count(pool))
    async with httpx.AsyncClient(headers={'Authorization':f'Bearer {tok}'}) as c:
        issues=await p._fetch_open_issues(c,base,org)
    print(len(issues),'open;',sum(1 for i in issues if p._match_rule(i,rules)),'matched')
asyncio.run(m())"
```

To find rules that have stopped firing, test each unmatched issue's title
against every rule's regex **ignoring the gates** — a regex that matches while
the rule didn't fire means a gate (usually `max_count`) blocked it.

Other traps worth knowing:

- **`is:unresolved` can desync from an issue's own `status`.** The list
  endpoint is backed by a search index that lags the primary record, so a new
  rule may take several probe cycles to visibly close something. Verify against
  the single-issue endpoint (`GET /api/0/issues/<id>/`, no org prefix) rather
  than trusting one list-endpoint miss.
- **Distinct issues can share a truncated alert title.** `notify_operator`
  truncates to 80 chars, so several different Ollama `APIConnectionError`
  signatures look identical in a Discord scan. Resolve the permalink/issue ID
  before concluding anything.
- **Repeat pages for one already-alerted issue** usually mean the brain
  restarted — per-issue dedupe lives in an in-process set. Documented behavior,
  not a bug.
- **Host reboots generate a burst of name-resolution errors.** Containers point
  at Docker's `127.0.0.11`, which forwards to the host's `systemd-resolved`; when
  that stops during shutdown, in-flight lookups fail. `TerminationSignal: 15` in
  the same window is the matching SIGTERM. Before adding yet another DNS
  suppression rule, check `last reboot` and
  `journalctl -u systemd-resolved -u tailscaled` for a teardown that explains
  the timestamps.

## Related

- [`brain/glitchtip_triage_probe.py`](../../brain/glitchtip_triage_probe.py) — the probe
- [`services/sentry_integration.py`](../../src/cofounder_agent/services/sentry_integration.py) — capture-side filter
- [Findings dashboard](http://localhost:3000/d/findings) — the _other_ signal
  path; a condition worth an operator's attention should be a
  [finding](../architecture/anti-hallucination.md), not just a captured
  exception.
