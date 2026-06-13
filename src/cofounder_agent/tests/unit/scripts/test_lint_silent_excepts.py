"""Tests for scripts/ci/lint_silent_excepts.py — the silent-failure guardrail.

Pins the detection contract: a handler whose body is only ``pass`` or
``<logger>.debug(...)`` counts as a silent swallow, while a handler that
records the failure (a second statement, a higher log level, a re-raise, or
a ``# noqa: silent-ok`` override) does not. This is the ratchet that stops
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
            "        pass  # noqa: silent-ok best-effort cleanup\n"
        )
        assert _scan_src(tmp_path, src) == 0

    def test_multiple_handlers_counted(self, tmp_path):
        src = (
            "def f():\n"
            "    try:\n        a()\n    except ValueError:\n        pass\n"
            "    try:\n        b()\n    except KeyError as e:\n        log.debug(e)\n"
        )
        assert _scan_src(tmp_path, src) == 2


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
