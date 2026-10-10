# Dependabot alerts vs the Trivy gate

GitHub can list dozens of open Dependabot alerts while the `Trivy — filesystem
vuln scan` gate in `security.yml` is green. On 2026-10-07 it was 55 alerts
against a green `main`. The two do not use different advisory databases:
Trivy's npm, pip, Poetry and uv entries come from the same GitHub Advisory
Database (`SeveritySource: ghsa` in its JSON output). The gap comes from what
each one filters out, and every one of the 55 was explained by the rows below.

## Why the gate passes while GitHub flags

| Filter             | Trivy gate                                                                                                                                                                                | Dependabot                                                                     | Hidden from the gate on 2026-10-07                                                              |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| Dev dependencies   | Skipped by default (npm `"dev": true`, Poetry non-main groups); the gate does not pass `--include-dev-deps`                                                                               | Alerts on them (`scope: development`)                                          | shell-quote (critical), brace-expansion, virtualenv, sharp in all five Cloudflare Workers       |
| Severity           | `HIGH,CRITICAL` only                                                                                                                                                                      | Every severity                                                                 | js-yaml, markdown-it, smol-toml, postcss-selector-parser, Mako, multidict (medium), katex (low) |
| No patched release | `ignore-unfixed: true` drops them                                                                                                                                                         | Alerts anyway                                                                  | nltk, braces, sprintf-js                                                                        |
| Advisory timing    | Trivy's DB is rebuilt every few hours and a cached copy is reused until its `NextUpdate`; the scan runs on a push or PR only when the `deps` classifier matches, plus the Monday baseline | GitHub alerts minutes after an advisory is published                           | sharp `GHSA-wq5f-xc86-pv6w` (high, runtime)                                                     |
| Classifier gaps    | Until 2026-10-07 the `deps` classifier matched only `src/cofounder_agent`'s Poetry pair, the root `package*.json` and `web/`                                                              | Watches every manifest                                                         | Any bump to `mcp-server*/uv.lock`, the brain's `poetry.lock` or a Worker lockfile               |
| Deleted manifests  | Scans only files that exist                                                                                                                                                               | Kept 23 alerts open against the repo-root `poetry.lock` after #3688 deleted it | —                                                                                               |

The fifth row is a configuration bug, now fixed. The classifier matches a
manifest or lockfile at any depth, and
`tests/unit/scripts/test_security_deps_classifier.py` checks every lockfile in
the tree against it.

The Workers' lockfiles hold nothing but dev dependencies (wrangler, vitest,
esbuild). The gate's report therefore does not list them at all, while
Dependabot alerts on every one.

The timing row is how `main` read green while being red. The sharp advisory
was published at 13:43Z on 2026-10-06. The Trivy DB that `main`'s last scan
used was built at 13:07Z the same day. Run on a DB downloaded the next
morning, the unchanged `main` failed the gate on sharp in the root and
`web/starter` lockfiles. A green `security.yml` run says the gate passed
against the DB it had at the time, and nothing newer.

## Reproduce each side's view

The gate, exactly as CI runs it. Use a fresh cache volume when the question is
"is `main` clean right now", because a cached DB is reused until its
`NextUpdate` and will not contain advisories newer than its build:

```bash
docker run --rm -v "$PWD":/src:ro -v trivy-cache-$(date -u +%F):/root/.cache aquasec/trivy:latest fs --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --skip-dirs '**/node_modules,**/.venv,**/__pycache__,**/.next,**/.vercel,**/dist,**/build,**/.pytest_cache,**/.claude' --skip-files .gitleaks-baseline.json /src
```

Roughly what Dependabot sees: the same command with `--include-dev-deps`
added and `--severity` and `--ignore-unfixed` removed.

GitHub's own list:

```bash
gh api "repos/Glad-Labs/poindexter/dependabot/alerts?state=open&per_page=100" --paginate --jq '.[] | "\(.number) \(.security_advisory.severity) \(.dependency.package.name) \(.dependency.manifest_path) \(.dependency.scope)"'
```

Triage by package, not by alert. One package can carry ten alerts (PyJWT did),
and the version you need is the highest `first_patched_version` across them.

## Why Dependabot cannot fix them itself

Dependabot's security-update job reports `conflicting-dependencies` when the
parent that pulls a vulnerable package pins it to an exact version or a narrow
range that excludes the fix. On 2026-10-07 every fixable alert was in that
state, because no parent had released a version with the fix. Each one is now
carried by an npm `overrides` entry. An override outlives its reason unless
someone removes it, so here is each one with the condition that retires it:

| Where                                           | Override                                                                   | Parent that blocks the fix                                     | Remove when                                                                                                     |
| ----------------------------------------------- | -------------------------------------------------------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| root `package.json`                             | `shell-quote: ^1.11.0`                                                     | `concurrently` 10.0.5 pins `1.9.0`                             | `concurrently` depends on `>=1.11.0`                                                                            |
| root `package.json`                             | `markdownlint-cli > js-yaml ^5.4.1, markdown-it ^14.3.1, smol-toml ^1.9.0` | `markdownlint-cli` 0.49.1 pins `~5.2.1` / `~14.3.0` / `~1.7.0` | `markdownlint-cli` ships wider ranges                                                                           |
| root `package.json`                             | `micromark-extension-math > katex ^0.18.2`                                 | `micromark-extension-math` 3.1.0 wants `^0.16.0`               | it accepts 0.18 (markdownlint parses math syntax and never renders it, so katex's API change does not reach it) |
| root `package.json`                             | `@tailwindcss/typography > postcss-selector-parser ^7.1.6`                 | typography 0.5.20 pins `6.0.10`                                | typography moves to 7.x                                                                                         |
| `web/starter/package.json`                      | `postcss-selector-parser: ^7.1.6`                                          | `tailwindcss` 3.4 wants `^6.1.2`, `postcss-nested` `^6.1.1`    | the starter moves to Tailwind 4, or Tailwind 3 accepts 7.x                                                      |
| each `infrastructure/cloudflare/*/package.json` | `sharp: ^0.35.5`                                                           | `miniflare` (via wrangler) pins `0.35.4`                       | the wrangler bump that moves miniflare past 0.35.4                                                              |

Check whether a parent has caught up with
`npm view <parent> dependencies.<pkg>`.

The postcss-selector-parser overrides cross a major version. 7.0.0's one
breaking change is that inserting nodes while iterating is now safe. Both
builds were compared before and after, and the output was byte for byte the
same: the public site's real stylesheet (70 KB, 68 `.prose` rules, Tailwind 4
and typography), and Tailwind 3 on `web/starter` plus an 81-rule battery of
selector-heavy variants (group/peer, arbitrary variants, `!important`,
`:has`, `dark`).

The root `sharp: ^0.35.0` override from #2873 was removed in the same change.
It existed because `next` asked for `^0.34.3` and there was no patched 0.34.x.
`next` 16.3 asks for `^0.35.4`, so the lockfile reaches 0.35.5 without it.

## Open by design

Alerts with no patched release stay open rather than being dismissed. A
dismissed alert never reopens, so dismissing one would also stop Dependabot
from opening the fix PR on the day a patch ships. The gate ignores them
through `ignore-unfixed`. As of 2026-10-07:

- **nltk** (high, `src/cofounder_agent/poetry.lock` and
  `mcp-server-voice/uv.lock`). Pulled in by `llama-index-core` and
  `pipecat-ai`; our code never imports it. The advisory covers model save and
  load helpers given caller-controlled paths.
- **braces** (high, root and `web/starter`). Reached through `micromatch` and
  `chokidar` in test and build tooling. A denial of service needs an
  attacker-supplied glob pattern.
- **sprintf-js** (medium, root). Reached through `argparse` in istanbul's
  config loader. Needs an attacker-controlled format string.

Dismiss an alert only when its manifest no longer exists (reason
`inaccurate`, with a comment naming the removed file and where the package
lives now). Anything with a patched release gets fixed.

## Writing the lockfiles

- **npm:** write lockfiles with npm 11 through `npx -y npm@11 ...`, the same
  major CI and Dependabot use. The host's npm 10.9 strips every `libc` field
  from a lockfile it rewrites. `npx -y npm@11 update <pkg> --package-lock-only
--ignore-scripts` moves a package within its range;
  `npx -y npm@11 install --package-lock-only --ignore-scripts` applies a new
  override, and if the package does not move, follow it with an `update` of
  that package. Confirm the diff touches only the target entries, then run
  `npm ci --dry-run` under both npm versions.
- **Poetry:** from `src/cofounder_agent`, `poetry update <pkg> --lock`, then
  `poetry check --lock`. Read the diff: Poetry 2.4 has occasionally rewritten
  the whole lock.
- **uv:** from the project directory, `uv lock --upgrade-package <pkg>`.
