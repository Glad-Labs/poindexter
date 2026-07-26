#!/usr/bin/env bash
#
# check-commit-signing.sh — warn (never block) if commit signing is off.
#
# Used by .pre-commit-config.yaml as a local hook. Always exits 0: the
# goal is to nudge contributors, not lock them out of an existing clone.
# Run scripts/setup-git-signing.sh to actually turn signing on.

set -u

YELLOW='\033[1;33m'
RED='\033[1;31m'
CYAN='\033[1;36m'
NC='\033[0m'

gpgsign="$(git config --get commit.gpgsign 2>/dev/null || echo false)"

if [ "${gpgsign}" != "true" ]; then
    printf "%b[WARN]%b commit.gpgsign is not enabled for this repo.\n" "${YELLOW}" "${NC}" >&2
    printf "       Your commit will land as %bUnverified%b on Gitea / GitHub.\n" "${RED}" "${NC}" >&2
    printf "       Fix:     %bbash scripts/setup-git-signing.sh%b\n" "${CYAN}" "${NC}" >&2
    printf "       Details: docs/operations/commit-signing.md\n" >&2
fi

# Never fail the commit — this is a nudge, not a gate.
exit 0
