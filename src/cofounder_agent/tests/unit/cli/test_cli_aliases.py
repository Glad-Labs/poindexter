"""Unit tests for ``poindexter.cli._aliases.deprecated_alias`` (#1652).

The CLI-consolidation work folds 9 flat top-level commands into noun-groups
(``gates`` / ``schedule``) and keeps the old flat names as **hidden,
deprecated aliases** for backcompat. ``deprecated_alias`` is the shim that
builds each alias: a hidden ``click.Command`` that reuses the canonical
command's params (so argv parses identically), prints a one-line deprecation
notice to **stderr** (keeping ``--json`` stdout clean for piping), and
delegates to the canonical command's callback.
"""

from __future__ import annotations

import click
from click.testing import CliRunner

from poindexter.cli._aliases import deprecated_alias


def _make_target():
    """A throwaway canonical command + a dict that records what it saw."""
    seen: dict = {}

    @click.command("realname")
    @click.argument("task_id")
    @click.option("--flag", is_flag=True)
    @click.option("--note", default=None)
    def target(task_id: str, flag: bool, note: str | None) -> None:
        seen.update(task_id=task_id, flag=flag, note=note)
        click.echo(f"ran {task_id} flag={flag} note={note}")

    return target, seen


def test_alias_is_hidden_and_named():
    target, _ = _make_target()
    alias = deprecated_alias(target, name="old-name", new_path="group realname")
    assert alias.hidden is True
    assert alias.name == "old-name"


def test_alias_parses_same_params_as_target():
    target, _ = _make_target()
    alias = deprecated_alias(target, name="old-name", new_path="x")
    assert {p.name for p in alias.params} == {p.name for p in target.params}


def test_alias_warns_to_stderr_and_delegates():
    target, seen = _make_target()
    alias = deprecated_alias(target, name="old-name", new_path="group realname")

    result = CliRunner().invoke(alias, ["t-1", "--flag", "--note", "hi"])

    assert result.exit_code == 0
    # Delegated to the canonical callback with the parsed argv.
    assert seen == {"task_id": "t-1", "flag": True, "note": "hi"}
    assert "ran t-1 flag=True note=hi" in result.output
    # Deprecation notice goes to stderr (so --json stdout stays clean) and
    # names both the old invocation and the canonical replacement.
    assert "deprecated" in result.stderr.lower()
    assert "old-name" in result.stderr
    assert "group realname" in result.stderr


def test_alias_keeps_stdout_clean_for_json_piping():
    """The warning must not land on stdout — a JSON consumer piping stdout
    must see only the canonical command's output.

    Click 8.2+ CliRunner always mixes stderr into result.output (mix_stderr
    was removed as a parameter and the mixed behaviour became permanent).
    result.stderr still captures stderr-only, so strip it from result.output
    to recover the stdout-only portion for the JSON assertion.
    """

    @click.command("realname")
    @click.option("--json", "json_output", is_flag=True)
    def target(json_output: bool) -> None:
        import json
        click.echo(json.dumps({"ok": True}))

    alias = deprecated_alias(target, name="old", new_path="grp realname")
    result = CliRunner().invoke(alias, ["--json"])
    assert result.exit_code == 0
    import json as _json
    # Deprecation warning must go to stderr (real-world piping stays clean).
    assert "deprecated" in result.stderr.lower()
    # Strip the stderr prefix from the mixed output to recover stdout-only.
    stdout_only = result.output[len(result.stderr):]
    assert _json.loads(stdout_only) == {"ok": True}  # stdout is pure JSON
