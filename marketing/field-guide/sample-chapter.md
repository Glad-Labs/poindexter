<!--
DRAFT — written by Claude from repo sources on 2026-09-26, for Matt to edit
for truth. Every fact below traces to scripts/ci/lib_scan_floor.py,
src/cofounder_agent/tests/unit/scripts/test_ci_lint_scan_floor.py,
docs/architecture/retention-backlog.md, or CLAUDE.md's scan-floor principle.
If an edit changes a number, re-check it against those sources first.
-->

# Chapter 4 — A check that scanned nothing has not passed

At the end of August, an audit copied every lint in my `scripts/ci` folder
into an empty directory and ran them one by one.

It was a deliberately stupid test. Each lint exists to scan part of the
codebase: a ratchet that blocks new security findings, a check that nobody
leaks an internal error string over HTTP, a guard on how secrets get
encrypted. In an empty directory there is nothing to scan. The only correct
answer any of them could give was an error.

Ten of the twelve printed "clean" and exited 0.

Three of those ten didn't even print a count. Their entire output was a
cheerful line saying the tree was fine. Nothing in what they printed told a
real scan of two thousand files apart from a scan of none.

Only one lint failed the way it should have. That was the moment I understood
that most of my CI gates would have kept reporting green on the day their
code moved out from under them.

## Worse than red

A failing check is annoying, but it's a signal. Somebody looks at it. A check
that has been silently disarmed is strictly worse: it reports success forever,
and the green it produces is indistinguishable from the green of a check doing
its job. It isn't just failing to protect you. It's actively telling you that
you are protected.

The gates weren't broken on the day of the audit. Their scan roots still
existed. But nothing would have caught the day they stopped existing, and that
day was coming.

## How a gate disarms itself

Nobody writes a lint that passes on an empty tree on purpose. It's the default
behaviour of the language.

Almost every one of those lints found the repo root the same way, walking a
fixed number of levels up from its own file, and then globbed for Python files
underneath:

```python
ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "src" / "cofounder_agent").rglob("*.py"):
    check(path)
print("clean")
```

If `src/cofounder_agent` is renamed, `rglob` doesn't raise. It yields nothing.
The loop body never runs, and the next line is the success message. That's
not a bug in any individual lint. It's what a loop over an empty sequence
does.

What makes this urgent in an agent-built codebase is how cheap structural
change has become. In the months before the audit, the whole content module
arrived as a physical code move, a core writer library relocated out of the
layer it used to live in, and one cleanup wave deleted entire directory trees.
Each of those was a routine, well-tested change, and each moved paths that
some check had hardcoded. The next rename of the backend root, or of the
watchdog's folder, would have turned ten gates permanently green, and nothing
in CI would have said so.

When a person restructures a codebase once a year, hardcoded paths are a
small risk. When an agent can restructure it in an evening, they're a
standing one.

## The fix is two function calls

Every lint now calls one of two guards from a small shared library,
`lib_scan_floor.py`, before it's allowed to declare anything clean:

```python
def require_dir(root: Path, *, lint: str) -> Path:
    """The scan root must exist."""
    if not root.is_dir():
        raise ScanFloorError(f"{lint}: scan root does not exist: {root}")
    return root


def require_scanned(count: int, *, lint: str, what: str = "files",
                    roots: Iterable[Path] = ()) -> int:
    """At least one item must actually have been examined."""
    if count <= 0:
        raise ScanFloorError(
            f"{lint}: examined 0 {what} — refusing to report clean."
            + _render_roots(roots)
        )
    return count
```

`require_dir` covers the renamed-folder case. `require_scanned` covers the
subtler one: the folder exists, but a glob stopped matching or the tree was
emptied. It goes immediately before the success message, so the lint can
only say "clean" after proving it looked at something.

Both guards print the roots they were looking in. "0 files scanned" is only
actionable when you can see _where_ it looked. Otherwise you get a red job and
a scavenger hunt.

## The ratchet on the ratchets

Fixing ten lints fixes ten lints. It does nothing about the eleventh, which a
session will write next month using the same natural loop.

So there's a test that does to every lint what the audit did once. For each
script in `scripts/ci`, it builds a throwaway tree where the repo-root idiom
lands on an empty directory, copies the lints in, runs each one, and asserts
a non-zero exit. A new lint is picked up automatically the day it's added. If
it forgets its floor, the build fails with a message naming the fix.

Two details there took me longer to get right than the guard itself.

**The test has its own floor.** A parametrised test over zero items passes
vacuously, so the first thing the file asserts is that it found at least ten
lints to check. If the lints ever move, the floor test fails loudly instead of
covering nothing — which is the exact bug it exists to prevent.

**Exemptions live in the lint, not in the test.** Some scripts in that folder
genuinely don't scan a source tree, so they need a way out. The first draft
kept a list of exempt filenames in the test. That broke immediately in a way I
hadn't predicted: the public mirror strips some operator-only lints, and a
shipping test that _named_ a stripped script tripped the mirror's safety check.
Now a lint opts out by carrying a `# scan-floor-exempt: <reason>` comment in
its own source. The exemption sits next to the code it describes, and adding
a lint never needs an edit anywhere else.

## Not every zero is a lie

The rule isn't "zero is always an error." Some zeros are real, and the craft
is telling a legitimate zero from a disarmed one.

One lint checks the version stamped in every `uv.lock` file. On the public
mirror, one of the directories that owns a lockfile is stripped, so that lint
correctly skips it there. If it floored on lockfiles _checked_, the mirror
would go red for doing the right thing. So it floors on lockfiles
_discovered_. It must find the files that should exist, even if it
legitimately declines to check some of them.

The Grafana panel lint had the opposite problem. By design, every connection
failure inside it is a skip or a warning, because a datasource being down
isn't the same as a panel being wrong. But "everything was skipped" then
exits 0. So its floor is per datasource: if a datasource is _configured_ and
validated zero of its own targets, that's a failure, however polite the
reasons for each individual skip were.

The question to ask of any zero is: **what would this number look like if the
thing producing it had died?** If the answer is "exactly the same", the zero
isn't evidence of anything.

## The same bug, running in production

The CI version of this problem is cheap to fix because CI is small. The
production version is where it gets expensive.

One of my retention policies, a job that prunes old pipeline checkpoints
every six hours, failed to delete roughly 20,000 rows it should have removed. It
failed for months, and it reported success on every run. Here is everything I
could see during that time:

| Signal                             | During the bug                     |
| ---------------------------------- | ---------------------------------- |
| `last_error`                       | `NULL` — the handler never errored |
| `last_run_at`                      | current — ran every six hours      |
| `total_deleted`                    | 15,807 — non-zero, looked healthy  |
| `last_run_deleted`                 | `0` — same as "nothing to do"      |
| The "all retention policies" panel | green                              |

The policy did exactly what it was told. What it was told was wrong.

Every one of those signals answers "did it run?" A misconfigured policy and
an idle one both run, both succeed and both delete nothing, so they produce
byte-identical telemetry. Around twenty of my policies legitimately delete
zero rows on most runs, so "deleted zero" can never be the alarm by itself.

The missing question was a correctness one: **how many rows should this
policy have removed, and hasn't?** A working policy drains that backlog to
about zero every run. A broken one lets it accumulate.

So retention handlers now declare that invariant: a backlog query registered
right next to the handler, where whoever writes the next handler will see it.
The alarm is the backlog _persisting_ across checks, not its size. A handler
that can't declare a backlog is reported as **unmonitored**, never as a
passing zero. That word matters. "Unmonitored" is honest about what you don't
know. A green zero pretends you do.

It's the scan floor again, one level up. The CI version asks "did you look at
anything?" The production version asks "did you measure the thing you're
actually responsible for, or just that you ran?"

## The rule

**Any check whose success condition is "found no problems" must also prove it
looked at something.** Three questions, asked of every gate, probe and panel
that can say "OK":

1. **What did it examine?** If you can't say, it can't either.
2. **How many?** Zero must be an error, or a zero you have explicitly
   justified.
3. **Where?** Print the location with the count, so a red result is a fix
   rather than a hunt.

## This evening

Adding this to your own repo takes about an hour:

1. **Find every success message.** Search your CI scripts for the strings
   they print when they pass ("clean", "OK", "no issues"). For each, ask
   whether execution can reach that line having examined nothing. In a loop
   over a glob, it almost always can.
2. **Add the two guards.** Put a `require_dir` where each script resolves its
   root, and a `require_scanned` directly before each success message.
3. **Add the empty-tree test,** so the next check someone writes, human or
   agent, inherits the rule without anyone remembering it. Give the test its
   own floor so it can't pass by checking nothing.
4. **Walk your dashboards once.** For every panel whose healthy state is zero
   or green, ask what it would show if the thing feeding it had quietly
   stopped. If the answer is "the same", it's measuring liveness. Find the
   invariant it should be measuring instead, or label it unmonitored.

None of this is sophisticated. That's rather the point. The most dangerous
failures in my system were never the ones that crashed. They were the ones
that kept saying everything was fine.
