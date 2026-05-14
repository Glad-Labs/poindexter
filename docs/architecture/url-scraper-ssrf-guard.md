# URL Scraper SSRF Guard

**Status:** active as of 2026-05-12
**Owner:** `services/url_scraper.py`
**Audit reference:** [`docs/security/audit-2026-05-12.md`](../security/audit-2026-05-12.md) P0 #5

## Threat model

`POST /api/topics/seed-url` is auth-required, but before this guard
shipped, `services/url_scraper.py` accepted any `http(s)://` URL and
followed redirects with no IP filter. The route fetched the page,
extracted title + content, and returned them in the task metadata
response.

An authenticated operator (or anyone who minted a token via the prior
open Dynamic Client Registration before audit P0 #3 closed it) could
drive scraping against internal-only endpoints exposed on the worker
host or its tailnet:

| Target                               | Risk                                         |
| ------------------------------------ | -------------------------------------------- |
| `http://127.0.0.1:9091/metrics`      | Prometheus internals + label cardinality     |
| `http://localhost:18443/`            | pgAdmin (auth-gated, but title leaks)        |
| `http://169.254.169.254/`            | Cloud-instance metadata (AWS/GCP/Azure IMDS) |
| `http://<tailnet-ip>:<port>`         | Any tailnet service the operator runs        |
| `http://10.0.0.0/8`, `192.168.x.x`   | LAN devices                                  |
| `http://[::1]:<port>`, `[fe80::]:..` | IPv6 equivalents of the above                |

The title + first few KB of the response body are exfiltrated through
the API response, regardless of whether the operator owns the internal
endpoint or just shares the tailnet.

A second attack: an attacker-controlled public page can `302` redirect
to one of the above IPs. Without per-hop re-checking, `httpx`'s
`follow_redirects=True` would chase the 302 and serve the internal
response to the caller.

## Defense

**Layer 1 — IP denylist before every HTTP request.**

`_resolve_and_check(url, site_config)` runs before the initial GET and
again at every redirect. It:

1. Parses the URL, extracts the hostname.
2. Short-circuits on literal IP hostnames (`http://127.0.0.1` is
   refused without a DNS round-trip).
3. Refuses the well-known loopback names `localhost` /
   `localhost.localdomain` regardless of `/etc/hosts`.
4. Resolves the hostname via `socket.getaddrinfo` (DNS-aware,
   IPv4 + IPv6) — never `socket.gethostbyname` (IPv4-only).
5. Checks every returned IP against the denylist. Any blocked IP
   short-circuits the request with `SSRFBlockedError`.

Resolved IPs and unparseable input both default to "blocked" — the
guard fails closed.

### Denylist

| CIDR             | Why                                        |
| ---------------- | ------------------------------------------ |
| `127.0.0.0/8`    | Loopback                                   |
| `10.0.0.0/8`     | RFC 1918 private                           |
| `172.16.0.0/12`  | RFC 1918 private                           |
| `192.168.0.0/16` | RFC 1918 private                           |
| `169.254.0.0/16` | Link-local + cloud metadata (IMDS)         |
| `100.64.0.0/10`  | CGNAT + Tailscale tailnet IPs              |
| `0.0.0.0/8`      | "This network" / unspecified               |
| `::1/128`        | IPv6 loopback                              |
| `fc00::/7`       | IPv6 unique-local (ULA, RFC 1918 equiv.)   |
| `fe80::/10`      | IPv6 link-local                            |
| `::/128`         | IPv6 unspecified                           |
| `::ffff:0:0/96`  | IPv4-mapped IPv6 — checked against the v4  |
|                  | denylist after extracting the embedded v4. |

**Layer 2 — manual redirect loop with per-hop re-check.**

`_safe_get(client, url)` replaces `httpx`'s built-in redirect follower.
For each hop it:

1. Calls `_resolve_and_check` on the _current_ URL.
2. Issues a single `client.get` (no auto-follow).
3. If the response is a 3xx with a `Location` header, computes the
   absolute target via `urljoin`, refuses non-http(s) schemes
   (e.g. `file://`, `gopher://`), and loops.
4. Caps the chain at `MAX_REDIRECTS = 5` hops.

Both `_fetch` (used by the generic + arXiv paths) and `_scrape_github`
(used for the api.github.com calls) construct their `httpx.AsyncClient`
with `follow_redirects=False` and route through `_safe_get`.

## Operator override

`app_settings.url_scraper_allow_internal_ips` (default `false`).

Flip to `true` when there's a legitimate internal-scraping need
(testing a staging instance on the LAN, scraping a local doc server
on `100.x.x.x`). The override short-circuits both `_resolve_and_check`
calls — DNS still happens but no IP is denied. Flip it back off when
done — leaving it on undoes the entire guard.

```bash
poindexter settings set url_scraper_allow_internal_ips true
# ...do the internal scrape via POST /api/topics/seed-url...
poindexter settings set url_scraper_allow_internal_ips false
```

## DNS rebinding posture

The current implementation resolves the hostname once before each
HTTP request and trusts the kernel + libc resolver to reuse that
answer when `httpx` opens the actual socket. An attacker who controls
DNS for the target hostname can return:

- a public IP at our `getaddrinfo` call (passes the gate)
- a private IP at the kernel's connect-time resolve (hits internal)

Closing this fully requires connecting to the resolved IP literal
and passing the original `Host:` header separately (or pinning the
connection via a custom `AsyncHTTPTransport`). That's deferred — see
the TODO in `_resolve_and_check`'s docstring. The current "resolve +
check before each hop" closes ~95% of the attack surface (every
redirect-based and literal-IP attack) and is reviewable in ~50 lines.

## Test coverage matrix

Tests live at `tests/unit/services/test_url_scraper_ssrf.py` (54 cases).

| Scenario                                         | Test class             |
| ------------------------------------------------ | ---------------------- |
| IP literal in denylist (v4)                      | `TestIsBlockedIp`      |
| IP literal in denylist (v6)                      | `TestIsBlockedIp`      |
| IPv4-mapped IPv6 (e.g. `::ffff:127.0.0.1`)       | `TestIsBlockedIp`      |
| Unparseable garbage IP                           | `TestIsBlockedIp`      |
| Public IPs (1.1.1.1, 8.8.8.8, github.com sample) | `TestIsBlockedIp`      |
| Reject loopback URL                              | `TestResolveAndCheck`  |
| Reject `localhost` hostname                      | `TestResolveAndCheck`  |
| Reject RFC 1918 via DNS                          | `TestResolveAndCheck`  |
| Reject cloud metadata `169.254.169.254`          | `TestResolveAndCheck`  |
| Reject Tailscale CGNAT (literal + DNS)           | `TestResolveAndCheck`  |
| Allow public IP via DNS                          | `TestResolveAndCheck`  |
| Reject multi-A record if any entry is private    | `TestResolveAndCheck`  |
| DNS failure surfaces `URLScrapeError`            | `TestResolveAndCheck`  |
| Override flag allows loopback                    | `TestResolveAndCheck`  |
| Override flag allows private DNS                 | `TestResolveAndCheck`  |
| Redirect chain ending at 127.0.0.1               | `TestSafeGetRedirects` |
| Redirect chain ending at 169.254.169.254         | `TestSafeGetRedirects` |
| Redirect chain ending at private DNS result      | `TestSafeGetRedirects` |
| Redirect to `file://`                            | `TestSafeGetRedirects` |
| Public redirect chain completes                  | `TestSafeGetRedirects` |
| Redirect chain hits `MAX_REDIRECTS` cap          | `TestSafeGetRedirects` |
| `scrape_url` rejects internal IPs at entry       | `TestScrapeUrlSSRF`    |
| `scrape_url` honors operator override end-to-end | `TestScrapeUrlSSRF`    |

All tests use `unittest.mock.patch` on `socket.getaddrinfo` and
`httpx.AsyncClient` — no real DNS or HTTP egress.

## Operational notes

- `SSRFBlockedError` subclasses `URLScrapeError`, so existing callers
  (`routes/topics_routes.py:71-73`) catch it via the existing `except
URLScrapeError` branch and surface a 400 with the explanatory
  message. No route-handler change required.
- The error message includes the override-flag instruction so an
  operator hitting the guard on a legitimate internal URL can self-serve.
- Run-time cost: one `getaddrinfo` call per HTTP hop. Negligible
  compared to the HTTP round-trip itself.
