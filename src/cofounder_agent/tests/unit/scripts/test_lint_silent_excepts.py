"""Tests for scripts/ci/lint_silent_excepts.py — the silent-failure guardrail.

Pins the detection contract: a handler whose body is only ``pass`` or
``<logger>.debug(...)`` counts as a silent swallow, while a handler that
records the failure (a second statement, a higher log level, a re-raise, or
a ``# silent-ok:`` override) does not. This is the ratchet that stops
the swallowed-exception category from growing (silent-failure audit H2).
"""
import importlib.util
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "lint_silent_excepts.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/lint_silent_excepts.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "lint_silent_excepts.py"
    spec = importlib.util.spec_from_file_location("lint_silent_excepts_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _scan_src(tmp_path: Path, src: str) -> int:
    f = tmp_path / "sample.py"
    f.write_text(src, encoding="utf-8")
    return LINT.scan_file(f)


class TestSilentDetection:
    def test_pass_only_is_silent(self, tmp_path):
        src = "def f():\n    try:\n        x()\n    except Exception:\n        pass\n"
        assert _scan_src(tmp_path, src) == 1

    def test_debug_only_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n"
            "        logger.debug('non-fatal: %s', e)\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_warning_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n"
            "        logger.warning('it broke: %s', e)\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_debug_plus_action_is_not_silent(self, tmp_path):
        # debug + emit_finding is the canonical fix — the failure is recorded.
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n"
            "        logger.debug('x: %s', e)\n"
            "        emit_finding(source='s', kind='k', title='t', body='b')\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_reraise_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception:\n        raise\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_silent_ok_override_exempts(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception:\n"
            "        pass  # silent-ok: best-effort cleanup\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_multiple_handlers_counted(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        a()\n    except ValueError:\n        pass\n"
            "    try:\n        b()\n    except KeyError as e:\n        log.debug(e)\n"
        )
        assert _scan_src(tmp_path, src) == 2


class TestBroadSuppressDetection:
    """``contextlib.suppress(Exception)`` is as silent as ``except: pass`` but
    lives in an ``ast.With`` node, not an ``ExceptHandler`` — the original
    scan could not see it. Broad suppression (Exception / BaseException) is
    counted; narrow, named suppression is a deliberate control-flow choice
    and is not."""

    def test_broad_suppress_exception_is_silent(self, tmp_path):
        src = (
            "from contextlib import suppress\n"
            "def f():\n"
            "    with suppress(Exception):\n"
            "        x()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_suppress_baseexception_is_silent(self, tmp_path):
        src = (
            "from contextlib import suppress\n"
            "def f():\n"
            "    with suppress(BaseException):\n"
            "        x()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_contextlib_suppress_attribute_form_is_silent(self, tmp_path):
        src = (
            "import contextlib\n"
            "def f():\n"
            "    with contextlib.suppress(Exception):\n"
            "        x()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_narrow_suppress_is_not_silent(self, tmp_path):
        src = (
            "from contextlib import suppress\n"
            "def f():\n"
            "    with suppress(OSError):\n"
            "        x()\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_narrow_suppress_multiple_types_is_not_silent(self, tmp_path):
        src = (
            "from contextlib import suppress\n"
            "def f():\n"
            "    with suppress(ValueError, TypeError):\n"
            "        x()\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_suppress_silent_ok_override_exempts(self, tmp_path):
        src = (
            "from contextlib import suppress\n"
            "def f():\n"
            "    with suppress(Exception):  # silent-ok: best-effort close\n"
            "        x()\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_suppress_and_silent_except_both_counted(self, tmp_path):
        src = (
            "from contextlib import suppress\n"
            "def f():\n"
            "    with suppress(Exception):\n"
            "        a()\n"
            "    try:\n        b()\n    except Exception:\n        pass\n"
        )
        assert _scan_src(tmp_path, src) == 2


class TestLowVisibilityLogDetection:
    """An ``except`` handler whose only action is a debug- or info-level log is
    below the operator's alerting bar: debug is below the prod Loki level
    (INFO), and info reaches Loki but sits below GlitchTip's ERROR event gate.
    Neither ever pages, so for an *exception* both count as silent — but
    warning+ does not."""

    def test_info_only_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n"
            "        logger.info('non-fatal: %s', e)\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_narrow_except_info_only_is_silent(self, tmp_path):
        # Consistent with the existing debug rule, which flags narrow excepts too.
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except KeyError as e:\n"
            "        log.info('missing: %s', e)\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_warning_still_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n"
            "        logger.warning('it broke: %s', e)\n"
        )
        assert _scan_src(tmp_path, src) == 0


class TestBroadExceptReturnSentinel:
    """A *broad* except (``except:``, ``except Exception``,
    ``except BaseException``) whose only statement returns a sentinel (None / a
    constant / an empty collection) swallows anything and reports "empty" with
    no log, no finding, no re-raise. A *narrow* except returning a sentinel
    (the ``except ValueError: return None`` parse-fallback idiom) is deliberate
    and is NOT flagged."""

    def test_broad_except_return_none_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return None\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_bare_except_return_none_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except:\n        return None\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_bare_return_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        do()\n"
            "    except Exception:\n        return\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_return_empty_collection_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return []\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_baseexception_return_none_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except BaseException:\n        return None\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_narrow_except_return_none_is_not_silent(self, tmp_path):
        # The deliberate parse-fallback idiom — left alone.
        src = (
            "def f(s):\n"
            "    try:\n        return int(s)\n"
            "    except ValueError:\n        return None\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_except_return_named_value_is_not_silent(self, tmp_path):
        # Returning a meaningful fallback value (not a bare sentinel) is more
        # likely deliberate; only constant / empty-literal returns are flagged.
        src = (
            "def f(default):\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return default\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_except_return_nonempty_literal_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return [1, 2]\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_except_multi_statement_return_not_counted(self, tmp_path):
        # Out of scope: a 2-statement ``debug(...); return None`` handler is a
        # separate blind spot. A second statement keeps it off this rule.
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception as e:\n"
            "        side_effect(e)\n"
            "        return None\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_except_silent_ok_override_exempts(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:  # silent-ok: optional read\n"
            "        return None\n"
        )
        assert _scan_src(tmp_path, src) == 0


class TestSentinelReturnCallForms:
    """``_is_sentinel_return`` also treats a zero-arg empty-constructor call —
    ``return set()`` / ``dict()`` / ``list()`` / ``tuple()`` / ``frozenset()``
    — as a sentinel: identical in silence to ``return []`` / ``return {}``,
    just spelled as a call instead of a literal. A call carrying an argument
    or keyword returns a meaningful (non-empty, or side-effecting) value and
    is deliberately NOT flagged."""

    def test_broad_except_return_set_call_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return set()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_return_dict_call_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return dict()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_return_list_call_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return list()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_return_tuple_call_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return tuple()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_return_frozenset_call_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return frozenset()\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_return_set_with_arg_is_not_silent(self, tmp_path):
        # A non-empty constructor call returns a meaningful value, not a
        # sentinel — same rule as the ``return [1, 2]`` literal case.
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return set(x)\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_except_return_dict_with_kwarg_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return dict(a=1)\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_broad_except_return_non_ctor_call_is_not_silent(self, tmp_path):
        # A zero-arg call to something other than the recognized empty-
        # collection constructors is a real fallback, not a sentinel.
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception:\n        return build_default()\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_narrow_except_return_set_call_is_not_silent(self, tmp_path):
        # Consistent with the existing narrow-except rule: deliberate control
        # flow, not a swallow.
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except ValueError:\n        return set()\n"
        )
        assert _scan_src(tmp_path, src) == 0


class TestBaselineRatchet:
    def test_real_tree_matches_baseline(self):
        """The committed baseline must satisfy the live tree (no drift).

        If this fails, either a silent handler was added without baselining,
        or one was removed and the baseline wasn't lowered — both are caught
        by the lint in CI, but this asserts the repo ships in a clean state.
        """
        counts = LINT.compute_counts()
        baseline = LINT.load_baseline()
        # Every file may not exceed its baseline (the lint's pass condition).
        offenders = {
            rel: (n, baseline.get(rel, 0))
            for rel, n in counts.items()
            if n > baseline.get(rel, 0)
        }
        assert offenders == {}, f"silent-except baseline drift: {offenders}"


class TestMultiStatementAndControlFlowSilence:
    """The linter's original matcher only saw single-statement handlers. A broad
    handler whose whole body is low-visibility is the same swallow spread over
    multiple statements, or hidden behind loop control flow."""

    def test_broad_except_debug_then_return_none_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception as e:\n"
            "        logger.debug('x: %s', e)\n"
            "        return None\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_info_then_return_empty_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception as e:\n"
            "        logger.info('x: %s', e)\n"
            "        return []\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_continue_is_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except Exception:\n            continue\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_debug_then_continue_is_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except Exception as e:\n"
            "            logger.debug('skip %s: %s', i, e)\n"
            "            continue\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_break_is_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except Exception:\n            break\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_ellipsis_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception:\n        ...\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_broad_except_print_is_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n        print('broke', e)\n"
        )
        assert _scan_src(tmp_path, src) == 1

    def test_narrow_except_continue_is_not_silent(self, tmp_path):
        src = (
            "def f(items):\n"
            "    for i in items:\n"
            "        try:\n            work(i)\n"
            "        except ValueError:\n            continue\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_debug_then_warning_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        x()\n"
            "    except Exception as e:\n"
            "        logger.debug('d: %s', e)\n"
            "        logger.warning('w: %s', e)\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_debug_then_emit_finding_then_return_none_is_not_silent(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        return g()\n"
            "    except Exception as e:\n"
            "        logger.debug('d: %s', e)\n"
            "        emit_finding(source='s', kind='k', title='t', body='b')\n"
            "        return None\n"
        )
        assert _scan_src(tmp_path, src) == 0


class TestVirtualenvIsNotScanned:
    """``mcp-server/.venv`` (created by ``uv run``) is not our code.

    Without the exclusion the walk scanned every installed dependency for
    swallowed exceptions, turning the ratchet red against third-party source
    on any machine that had run the mcp-server suite.
    """

    def test_mcp_server_root_excludes_venv(self):
        root, excluded = next(
            (r, e) for r, e in LINT.SCAN_ROOTS if r.name == "mcp-server"
        )
        assert ".venv" in excluded

    def test_a_materialised_venv_is_skipped(self, tmp_path, monkeypatch):
        root = tmp_path / "mcp-server"
        pkg = root / ".venv" / "lib" / "site-packages" / "dep"
        pkg.mkdir(parents=True)
        (pkg / "quiet.py").write_text(
            "def f():\n    try:\n        g()\n    except Exception:\n        pass\n",
            encoding="utf-8",
        )
        (root / "server.py").write_text("def tool():\n    return 1\n", encoding="utf-8")

        # Reuse the REAL exclusion tuple — restating it here would make the
        # test pass even after someone deleted ".venv" from SCAN_ROOTS.
        excluded = next(e for r, e in LINT.SCAN_ROOTS if r.name == "mcp-server")
        monkeypatch.setattr(LINT, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(LINT, "SCAN_ROOTS", [(root, excluded)])
        assert LINT.compute_counts() == {}
