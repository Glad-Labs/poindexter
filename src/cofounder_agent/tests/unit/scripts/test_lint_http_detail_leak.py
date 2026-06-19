"""Tests for scripts/ci/lint_http_detail_leak.py — the error-detail-leak guard.

Pins the detection contract for the information-disclosure pattern from
poindexter#724: an ``HTTPException`` whose ``detail=`` is an f-string that
interpolates a caught exception leaks internal detail (resolved IPs from an
httpx ``ConnectError``, which JWT claim failed, ...) straight to the API
client. The fix is a generic ``detail=`` + a server-side log; this ratchet
stops the pattern from re-sprouting (it already had to be cleaned out twice:
#642, then #724).

The detector keys on the ``HTTPException`` *call* — NOT the ``detail=``
substring — because dozens of internal result dataclasses
(``JobResult(detail=f"query failed: {e}")``) share the keyword name but never
reach a client. Those MUST NOT be flagged.
"""
import importlib.util
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "lint_http_detail_leak.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/lint_http_detail_leak.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "lint_http_detail_leak.py"
    spec = importlib.util.spec_from_file_location("lint_http_detail_leak_under_test", path)
    assert spec and spec.loader, "could not build import spec for the lint module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _scan(src: str) -> list[int]:
    return LINT.scan_source(src)


class TestLeakDetection:
    def test_fstring_interpolating_caught_exception_is_flagged(self):
        src = (
            "def f():\n"
            "    try:\n        scrape()\n"
            "    except ValueError as e:\n"
            "        raise HTTPException(status_code=400, detail=f'failed: {e}') from e\n"
        )
        assert len(_scan(src)) == 1

    def test_generic_detail_is_clean(self):
        src = (
            "def f():\n"
            "    try:\n        scrape()\n"
            "    except ValueError as e:\n"
            "        logger.warning('scrape failed: %s', e)\n"
            "        raise HTTPException(status_code=400, detail='Could not scrape URL') from e\n"
        )
        assert _scan(src) == []

    def test_str_and_type_forms_are_flagged(self):
        src = (
            "def f():\n"
            "    try:\n        scrape()\n"
            "    except Exception as exc:\n"
            "        raise HTTPException(status_code=500, detail=f'{type(exc).__name__}: {exc}')\n"
        )
        assert len(_scan(src)) == 1

    def test_conversion_form_is_flagged(self):
        src = (
            "def f():\n"
            "    try:\n        scrape()\n"
            "    except Exception as e:\n"
            "        raise HTTPException(status_code=500, detail=f'boom {e!r}')\n"
        )
        assert len(_scan(src)) == 1

    def test_attribute_callee_is_flagged(self):
        src = (
            "def f():\n"
            "    try:\n        scrape()\n"
            "    except Exception as e:\n"
            "        raise fastapi.HTTPException(status_code=500, detail=f'boom {e}')\n"
        )
        assert len(_scan(src)) == 1


class TestNoFalsePositives:
    def test_jobresult_dataclass_detail_is_not_flagged(self):
        # The dominant shape across the codebase: `detail` is a field on an
        # internal result dataclass, never serialized to a client.
        src = (
            "def f():\n"
            "    try:\n        run()\n"
            "    except Exception as e:\n"
            "        return JobResult(ok=False, detail=f'query failed: {e}', changes_made=0)\n"
        )
        assert _scan(src) == []

    def test_httpexception_non_exception_interpolation_is_clean(self):
        # Interpolating a non-exception local (task_id) is not a leak.
        src = (
            "def f(task_id):\n"
            "    raise HTTPException(status_code=404, detail=f'task {task_id} not found')\n"
        )
        assert _scan(src) == []

    def test_httpexception_outside_except_is_clean(self):
        src = (
            "def f():\n"
            "    raise HTTPException(status_code=400, detail='bad request')\n"
        )
        assert _scan(src) == []


class TestOverride:
    def test_noqa_detail_ok_suppresses(self):
        src = (
            "def f():\n"
            "    try:\n        scrape()\n"
            "    except Exception as e:\n"
            "        raise HTTPException(status_code=500, detail=f'boom {e}')  # noqa: detail-ok deliberate\n"
        )
        assert _scan(src) == []


class TestScanFile:
    def test_scan_file_round_trips(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text(
            "def f():\n"
            "    try:\n        scrape()\n"
            "    except Exception as e:\n"
            "        raise HTTPException(status_code=500, detail=f'boom {e}')\n",
            encoding="utf-8",
        )
        assert len(LINT.scan_file(f)) == 1
