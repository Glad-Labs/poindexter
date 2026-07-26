#!/usr/bin/env bash
#
# Setup Git Signing — configures this clone to GPG-sign commits and tags.
#
# Usage:
#   bash scripts/setup-git-signing.sh               # use existing user.signingkey
#   bash scripts/setup-git-signing.sh <KEYID>       # set the signing key, too
#
# What it does:
#   1. Verifies `gpg` is installed and a usable secret key exists
#   2. Sets `commit.gpgsign=true` and `tag.gpgsign=true` at the repo scope
#   3. Sets `gpg.program` if GPG is in a non-standard location
#   4. Verifies by creating a throwaway signed empty commit in a temp branch,
#      then rolling back — fails loud if signing doesn't actually work
#
# Fails loud — no silent fallbacks. If this script exits non-zero, your
# commits will NOT be signed and the repo is unchanged beyond local config.
#
# See docs/operations/commit-signing.md for full background (key generation,
# Gitea/GitHub setup, troubleshooting).

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { printf "%b[INFO]%b  %s\n" "$BLUE"   "$NC" "$*"; }
ok()    { printf "%b[OK]%b    %s\n" "$GREEN"  "$NC" "$*"; }
warn()  { printf "%b[WARN]%b  %s\n" "$YELLOW" "$NC" "$*"; }
fatal() { printf "%b[FATAL]%b %s\n" "$RED"    "$NC" "$*" >&2; exit 1; }

# --- Preflight -----------------------------------------------------------

command -v git >/dev/null 2>&1 || fatal "git not found on PATH"
command -v gpg >/dev/null 2>&1 || fatal "gpg not found on PATH — install GnuPG first (https://gnupg.org)"

# Must be inside a git repo.
git rev-parse --git-dir >/dev/null 2>&1 || fatal "not inside a git working tree"

# --- Key selection -------------------------------------------------------

KEY_ID="${1:-}"
if [ -z "${KEY_ID}" ]; then
    KEY_ID="$(git config --get user.signingkey || true)"
fi

if [ -z "${KEY_ID}" ]; then
    warn "no user.signingkey configured and none passed on the command line"
    warn "available GPG secret keys:"
    gpg --list-secret-keys --keyid-format=long || true
    fatal "pass a key id as the first arg, or run: git config user.signingkey <KEYID>"
fi

# Confirm the key actually exists and is usable for signing.
if ! gpg --list-secret-keys "${KEY_ID}" >/dev/null 2>&1; then
    fatal "GPG cannot find secret key ${KEY_ID} — check \`gpg --list-secret-keys\`"
fi

info "using signing key: ${KEY_ID}"

# --- Apply config --------------------------------------------------------

git config user.signingkey "${KEY_ID}"
git config commit.gpgsign true
git config tag.gpgsign true

# On Windows + Git for Windows, gpg.program sometimes needs to point at
# Kleopatra's gpg.exe. We only set it if the default isn't already working.
if [ -n "${GPG_PROGRAM:-}" ]; then
    git config gpg.program "${GPG_PROGRAM}"
    info "set gpg.program=${GPG_PROGRAM}"
fi

ok "repo git config updated: commit.gpgsign=true, tag.gpgsign=true, user.signingkey=${KEY_ID}"

# --- Verification: sign a throwaway commit -------------------------------

info "verifying signing works with a throwaway commit..."

# Use a detached commit tree so we don't touch any branch.
TMP_TREE="$(git write-tree)"
if ! SIGNED_SHA="$(git commit-tree "${TMP_TREE}" -m 'signing-test' -S 2>/dev/null)"; then
    fatal "GPG signing failed — check your gpg-agent, passphrase cache, and key permissions"
fi

if ! git verify-commit "${SIGNED_SHA}" >/dev/null 2>&1; then
    fatal "commit was created but git verify-commit rejected it — key/trust is misconfigured"
fi

ok "verified: git can sign and git verify-commit accepts the signature"
ok "setup complete — your commits and tags in this clone will now be signed"
