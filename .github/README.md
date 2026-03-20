# GitHub Automation Notes

This directory contains repository automation assets (workflows, helper scripts, templates).

## Workflow Security Policy: Full SHA Pinning

All external GitHub Actions in `.github/workflows` must be pinned to a full 40-character commit SHA.

- Allowed:
  - `uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5`
  - `uses: ./path/to/local-action`
  - `uses: docker://alpine:3.20`
- Not allowed:
  - `uses: actions/checkout@v4`
  - `uses: actions/setup-node@main`

This protects CI from mutable tags/branches and aligns with enterprise action restrictions.

## Enforcement

Policy enforcement runs in:

- `.github/workflows/action-sha-guard.yml`

The guard fails CI if any remote action reference is not a full commit SHA.

## Updating an Action Safely

1. Resolve the current commit for the upstream tag:

```bash
git ls-remote https://github.com/actions/checkout refs/tags/v4 refs/tags/v4^{}
```

1. Update `uses:` to that full SHA.
1. Keep the human-readable comment suffix for maintainability, for example `# v4`.
1. Open a PR and ensure `Action SHA Guard` passes.

## Local Verification

Run this from repo root to find any non-SHA action refs:

```bash
rg -n "^\s*uses:\s*(?!\.\/|docker:\/\/)[^@\s]+@(?![0-9a-fA-F]{40})([^\s#]+)" .github/workflows
```

If the command returns no matches, all action refs are SHA-pinned.
