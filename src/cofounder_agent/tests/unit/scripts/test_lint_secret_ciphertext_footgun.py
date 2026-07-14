"""Tests for scripts/ci/lint_secret_ciphertext_footgun.py — the ciphertext-footgun guard.

Pins the detection contract for the security-audit finding from #2131:
``SiteConfig`` filters ``is_secret=true`` rows out of its sync in-memory
cache, so ``site_config.get("some_secret_key", default)`` silently returns
the ``enc:v1:...`` ciphertext blob instead of the plaintext value or a
missing-config error. Three separate 2026-04-23 incidents (Alertmanager
dispatch, Vercel ISR, auto-Telegram post-publish) hit this exact bug and
were each fixed ad hoc (GH-107) — the tell that the durable fix is a
ratchet, not another manual sweep.

The detector keys on the receiver name (``site_config`` / ``_site_config``)
plus a secret-shaped key literal — NOT any ``.get(...)`` call — because
dozens of legitimate ``dict.get(...)`` / ``os.environ.get(...)`` calls in
the codebase use similarly-named keys and never touch SiteConfig's cache.
"""
import importlib.util
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "lint_secret_ciphertext_footgun.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/lint_secret_ciphertext_footgun.py")


def _load_lint_module():
    path = (
        _find_repo_root(Path(__file__))
        / "scripts"
        / "ci"
        / "lint_secret_ciphertext_footgun.py"
    )
    spec = importlib.util.spec_from_file_location(
        "lint_secret_ciphertext_footgun_under_test", path
    )
    assert spec and spec.loader, "could not build import spec for the lint module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _scan(src: str) -> list[tuple[int, str]]:
    return LINT.scan_source(src)


class TestLeakDetection:
    def test_site_config_get_on_secret_key_is_flagged(self):
        src = "def f(site_config):\n    return site_config.get('langfuse_secret_key', '')\n"
        assert len(_scan(src)) == 1

    def test_self_underscore_site_config_attribute_form_is_flagged(self):
        src = (
            "class Svc:\n"
            "    def f(self):\n"
            "        return self._site_config.get('openclaw_webhook_token', '')\n"
        )
        assert len(_scan(src)) == 1

    def test_self_site_config_attribute_form_is_flagged(self):
        src = (
            "class Svc:\n"
            "    def f(self):\n"
            "        return self.site_config.get('discord_ops_webhook_url', '')\n"
        )
        assert len(_scan(src)) == 1

    def test_flags_the_secret_key_name_in_the_result(self):
        src = "def f(site_config):\n    return site_config.get('api_key', '')\n"
        found = _scan(src)
        assert found == [(2, "api_key")]


class TestNoFalsePositives:
    def test_non_secret_key_is_clean(self):
        src = "def f(site_config):\n    return site_config.get('writer_max_inline_images', 3)\n"
        assert _scan(src) == []

    def test_plain_dict_get_with_secret_shaped_key_is_clean(self):
        # Not a SiteConfig receiver — a bare dict/os.environ .get() is fine.
        src = "def f(config):\n    return config.get('api_key', '')\n"
        assert _scan(src) == []

    def test_os_environ_get_is_clean(self):
        src = "import os\ndef f():\n    return os.environ.get('api_key', '')\n"
        assert _scan(src) == []

    def test_get_secret_method_name_is_not_flagged(self):
        # Different method name entirely — this IS the correct call.
        src = "async def f(site_config):\n    return await site_config.get_secret('api_key', '')\n"
        assert _scan(src) == []

    def test_get_bool_get_int_get_float_are_not_flagged(self):
        src = (
            "def f(site_config):\n"
            "    a = site_config.get_bool('some_token_flag', False)\n"
            "    b = site_config.get_int('some_token_count', 0)\n"
            "    c = site_config.get_float('some_token_ratio', 0.0)\n"
            "    return a, b, c\n"
        )
        assert _scan(src) == []

    def test_non_literal_key_is_clean(self):
        # Can't statically prove the key is secret-shaped, don't flag.
        src = "def f(site_config, key):\n    return site_config.get(key, '')\n"
        assert _scan(src) == []


class TestOverride:
    def test_secret_get_ok_marker_suppresses(self):
        src = (
            "def f(site_config):\n"
            "    return site_config.get('webhook_url', '')  # secret-get-ok: is_secret=false, plain URL\n"
        )
        assert _scan(src) == []


class TestScanFile:
    def test_scan_file_round_trips(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text(
            "def f(site_config):\n    return site_config.get('api_key', '')\n",
            encoding="utf-8",
        )
        assert len(LINT.scan_file(f)) == 1

    def test_scan_file_on_missing_file_returns_empty(self, tmp_path):
        assert LINT.scan_file(tmp_path / "does_not_exist.py") == []


class TestMainAllowlist:
    def test_secret_resolver_module_itself_is_excluded_from_scan_roots(self):
        # services/integrations/secret_resolver.py legitimately names
        # secret-shaped keys while implementing the correct get_secret path.
        assert "services/integrations/secret_resolver.py" in LINT.ALLOWED_FILES
