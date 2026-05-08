# Commit Signing

Poindexter expects every commit on `main` (and every tag) to be GPG-signed.
Signed commits show as **Verified** on GitHub and prove the commit actually
came from the author's key, not a compromised account or a typosquatted
identity.

This page covers:

1. Generating a GPG key (if you don't already have one)
2. Telling git about the key
3. Configuring this clone to sign by default (`scripts/setup-git-signing.sh`)
4. Registering the public key with GitHub so the Verified badge appears
5. Troubleshooting

## 1. Generate a GPG key (skip if you already have one)

```sh
gpg --full-generate-key
# Choose RSA + RSA, 4096 bits, no expiry (or whatever you prefer).
# Use the same email you commit with — it must match `git config user.email`.
```

List your keys:

```sh
gpg --list-secret-keys --keyid-format=long
```

Look for the `sec` line — the long hex string after `rsa4096/` is your key ID.

## 2. Tell git about the key

```sh
git config --global user.signingkey <YOUR_KEY_ID>
git config --global user.email      <the email on the key>
```

Leaving `user.signingkey` at the global scope means every repo can use it;
the per-repo `commit.gpgsign=true` flag is what actually turns signing on.

## 3. Turn signing on for this clone

From the repo root:

```sh
bash scripts/setup-git-signing.sh
```

The script:

- verifies `gpg` is installed and the key exists
- sets `commit.gpgsign=true` and `tag.gpgsign=true` at the repo scope
- creates a throwaway signed commit to prove signing actually works, then
  discards it — **fails loud** if signing is misconfigured

If you haven't set `user.signingkey` globally yet, pass the key id directly:

```sh
bash scripts/setup-git-signing.sh 7169605F62C751356D054A26A821E680E5FA6305
```

On Windows, if `gpg` is installed but git can't find it, export `GPG_PROGRAM`
to the full path before running the script:

```sh
GPG_PROGRAM="C:/Program Files (x86)/GnuPG/bin/gpg.exe" \
  bash scripts/setup-git-signing.sh
```

## 4. Register the public key with GitHub

Export your public key:

```sh
gpg --armor --export <YOUR_KEY_ID>
```

Paste the full block (including the `-----BEGIN PGP PUBLIC KEY BLOCK-----`
and `-----END…-----` lines) into **GitHub** — Settings → SSH and GPG keys
→ _New GPG key_ (`https://github.com/settings/keys`).

Once registered, commits signed with that key show a green **Verified**
badge in the UI.

## 5. What about CI commits?

Two workflows create commits automatically:

- **`release-please`** (`.github/workflows/release-please.yml`) — the
  `release-please-action` creates release PRs via the GitHub API using the
  `GITHUB_TOKEN`. API-created commits are **signed by GitHub's own key** and
  show Verified automatically. No extra config needed.
- **Scheduled Claude sessions** (`scripts/claude-sessions.ps1`) — these run
  locally on Matt's workstation under his git identity, so they pick up
  whatever `commit.gpgsign` is set to in the clone. Running
  `scripts/setup-git-signing.sh` once is enough.

There is currently no GitHub Actions workflow that commits back to
the repo apart from `release-please` (covered above). If one is added
later, it must either use the GitHub API (server-signed) or import a bot
key and set `GPG_KEY` + `commit.gpgsign=true` in the job.

## 6. Troubleshooting

**`gpg: signing failed: Inappropriate ioctl for device`**
GPG can't prompt for your passphrase because there's no TTY. Add to your
shell rc:

```sh
export GPG_TTY=$(tty)
```

**`error: gpg failed to sign the data`**
Your `gpg-agent` probably isn't running or can't reach its socket. Restart
it: `gpgconf --kill gpg-agent && gpgconf --launch gpg-agent`.

**Commits show Unverified on GitHub even though signing worked locally**
The email on your GPG key (`gpg --list-keys`) must exactly match the email
on the commit. If they differ, GitHub marks the commit _signed but
unverified_. Either re-key with the correct email or edit your local
`user.email`.

**`git verify-commit` says `gpg: Can't check signature: No public key`**
You're trying to verify a commit signed by someone else whose public key
you don't have. Import it: `gpg --recv-keys <KEY_ID>`.

## 7. Enforcement

Signing is currently **expected but not hard-blocked** — the pre-commit
hook emits a warning on unsigned commits but doesn't reject them. GitHub
branch protection for `main` should eventually require signed commits (see
GH-29 follow-up). Until then, please check your own PRs show Verified
before requesting review.
