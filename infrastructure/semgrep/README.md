# Vendored Semgrep rulesets

`p-python.yaml` (151 rules) and `p-secrets.yaml` (52 rules), resolved from the
Semgrep registry and committed here on **2026-08-28**.

## Why vendored rather than `--config p/python`

Two reasons, both learned the hard way this week.

**No network dependency.** `--config p/python` fetches from `semgrep.dev` on
every run. A registry outage would turn a security gate red for a reason that
has nothing to do with the code — and a gate that reddens for unrelated reasons
is a gate people learn to ignore. That is the exact failure this ratchet was
added to avoid (see the poindexter#1029 follow-on audit).

**Pinned rules.** Registry packs change under you. A rule added upstream
overnight can redden a tree that nobody touched, which is how the bandit wave
buried 18 real issues under 91 false positives. Vendoring means a rule change
arrives as a reviewable diff in a PR, on your schedule.

Verified equivalent at vendoring time: registry and vendored configs produced
an identical 47-finding set over `src/cofounder_agent`, `brain`, and `scripts`.

## Refreshing

Deliberately manual — there is no auto-update, because an unattended rule bump
is the thing being avoided.

```bash
curl -sfL https://semgrep.dev/c/p/python  -o infrastructure/semgrep/p-python.yaml
curl -sfL https://semgrep.dev/c/p/secrets -o infrastructure/semgrep/p-secrets.yaml
python scripts/ci/semgrep_lint.py            # see what the new rules find
python scripts/ci/semgrep_lint.py --update-baseline   # only after triaging
```

Read the new findings before re-baselining. A refresh that ends in a blind
`--update-baseline` grandfathers whatever the new rules caught, which defeats
the point of refreshing.

## Why these two packs

`p/python` covers the language-level classes (injection, unsafe deserialization,
weak crypto, subprocess misuse). `p/secrets` covers hardcoded credentials, which
matters most for the operator overlay — `modules/finance/`, `operator_*.py` and
the sessions tap are stripped from the public mirror, so the mirror's own CodeQL
never sees them, and GitHub code scanning is disabled on the private repos.
This ratchet is what covers that code.
