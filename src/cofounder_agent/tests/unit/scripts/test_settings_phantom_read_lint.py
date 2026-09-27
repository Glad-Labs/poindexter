"""Tests for scripts/ci/settings_phantom_read_lint.py — the phantom-settings-
read ratchet.

A phantom read is a literal ``app_settings`` key passed to a settings reader
in production code that no seed source (``settings_defaults.py`` /
``0000_baseline.seeds.sql`` / ``brain/seed_app_settings.json``) defines — it
can never be tuned, and every read silently falls back to whatever default is
baked into the call site. Motivating bug: ``gpu_scheduler.py`` read
``electricity_rate_kwh_usd``, which nothing seeds, while the real,
EIA-maintained ``electricity_rate_kwh`` sat unread (glad-labs-stack#4065).

``scan_source`` is a pure function of source text (no filesystem, no seed
lookup), mirroring ``atom_independence_lint.scan_source`` — most of the
matching-shape tests below exercise it directly. ``find_phantom_reads`` does
the seed cross-reference and is exercised against a small fake tree so the
"seeded read passes, unseeded read fails" contract is tested end to end,
per this lint's own design doc (a phantom-read finding needs a REAL seed
source to compare against, not just an AST match).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "settings_phantom_read_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/settings_phantom_read_lint.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "settings_phantom_read_lint.py"
    spec = importlib.util.spec_from_file_location("settings_phantom_read_lint_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _keys(src: str) -> list[str]:
    """Just the matched key literals from scan_source, dropping the secret flag."""
    return [k for k, _via_secret in LINT.scan_source(src)]


class TestBareNameHelpers:
    """`_cfg_int` / `_cfg_float` / `_cfg_bool` / `_sc_get` / `_sc_get_di` —
    re-implemented per file with two different signatures in the wild."""

    def test_key_first_shape_matches(self):
        # gpu_scheduler.py's own shape: _cfg_float(key, default).
        src = "electricity_rate = _cfg_float('electricity_rate_kwh_usd', 0.12)\n"
        assert _keys(src) == ["electricity_rate_kwh_usd"]

    def test_site_config_first_shape_matches(self):
        # qa_audio.py's shape: _cfg_float(site_config, key, default).
        src = "max_sil = _cfg_float(site_config, 'media.qa.audio.max_silence_s', 3.0)\n"
        assert _keys(src) == ["media.qa.audio.max_silence_s"]

    def test_sc_get_di_matches(self):
        src = "v = _sc_get_di('ollama_base_url', site_config=site_config)\n"
        assert _keys(src) == ["ollama_base_url"]

    def test_both_args_dynamic_matches_nothing(self):
        # Inside the helper's OWN body: `_sc().get(key, default)` calls the
        # helper's parameters straight through — neither is a literal.
        src = "def _cfg_int(key, default):\n    return int(_sc_get(key, default))\n"
        assert _keys(src) == []

    def test_unrelated_function_of_the_same_name_pattern_is_not_special_cased(self):
        # `_cfg_int` is matched by NAME alone (documented tradeoff — see the
        # lint's module docstring). A same-named helper for something else
        # entirely would still match; this test pins that this is a known,
        # accepted, ratchet-not-issue-filer tradeoff, not an oversight.
        src = "n = _cfg_int('not_a_settings_key_at_all', 5)\n"
        assert _keys(src) == ["not_a_settings_key_at_all"]


class TestAttributeAccessors:
    """`.get_int` / `.get_float` / `.get_bool` / `.get_secret` / `.get_list`
    on any receiver; bare `.get` only on a settings-shaped receiver name."""

    def test_get_int_matches_on_any_receiver_name(self):
        src = "n = self._platform.config.get_int('qa_gate_max_tokens', 600)\n"
        assert _keys(src) == ["qa_gate_max_tokens"]

    def test_get_secret_is_flagged_as_secret(self):
        src = "k = await site_config.get_secret('pexels_api_key', '')\n"
        (key, via_secret), = LINT.scan_source(src)
        assert key == "pexels_api_key"
        assert via_secret is True

    def test_non_secret_accessor_is_not_flagged_as_secret(self):
        src = "v = site_config.get('site_title', '')\n"
        (key, via_secret), = LINT.scan_source(src)
        assert key == "site_title"
        assert via_secret is False

    def test_bare_get_matches_on_site_config_receiver(self):
        src = "v = site_config.get('crawler_contact_url', '')\n"
        assert _keys(src) == ["crawler_contact_url"]

    def test_bare_get_matches_on_self_underscore_site_config_receiver(self):
        src = "v = self._site_config.get('image_gen_server_url', '')\n"
        assert _keys(src) == ["image_gen_server_url"]

    def test_bare_get_does_not_match_on_a_plain_dict_receiver(self):
        # `config`/`context`/`state`/`row` are ordinary dicts throughout this
        # codebase (e.g. `config.get("_site_config")` retrieves the REAL
        # SiteConfig instance out of a pipeline context dict) — an
        # unrestricted receiver would drown real findings in dict-access noise.
        src = "sc = config.get('_site_config')\nname = row.get('slug')\n"
        assert _keys(src) == []


class TestNoFallthroughOnAttributeCalls:
    """Regression test for the exact bug this lint shipped with a fix for:
    an attribute call's default value must never be mistaken for its key
    when the real key is a dynamic (non-literal) expression."""

    def test_dynamic_key_with_string_default_matches_nothing(self):
        src = "opted_in = site_config.get(f'{slot}_auto_promote', 'false')\n"
        assert _keys(src) == []

    def test_dynamic_key_via_function_call_with_string_default_matches_nothing(self):
        src = "raw = site_config.get(_gate_setting_key(gate_name), 'off')\n"
        assert _keys(src) == []

    def test_literal_key_at_position_zero_still_matches(self):
        # Sanity check the fix didn't also break the ordinary, common case.
        src = "raw = site_config.get('qa_web_factcheck_enabled', 'true')\n"
        assert _keys(src) == ["qa_web_factcheck_enabled"]


class TestKeyShapeFilter:
    def test_non_key_shaped_literal_is_ignored(self):
        src = "site_config.get_int('', 5)\n"
        assert _keys(src) == []

    def test_boolean_words_are_key_shaped_so_no_fallthrough_is_load_bearing(self):
        # `false`/`off`/`true` PASS the shape filter — they are lowercase
        # letters. The only thing stopping a dynamic-key call's default from
        # being read as its key is the no-fallthrough rule above, not this
        # filter. (An earlier version of this file claimed the filter was a
        # backstop for exactly that case. It never was.)
        assert all(LINT.KEY_SHAPE.match(w) for w in ("false", "off", "true"))

    def test_uppercase_literal_is_ignored(self):
        src = "site_config.get('NOT_THE_APP_SETTINGS_CONVENTION', '')\n"
        assert _keys(src) == []

    def test_dotted_namespace_key_matches(self):
        src = "site_config.get('plugin.llm_provider.anthropic.enabled', '')\n"
        assert _keys(src) == ["plugin.llm_provider.anthropic.enabled"]


WIDGET_REL = "src/cofounder_agent/poindexter/widget_service.py"


def _use_fake_tree(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Write a minimal repo tree with one read per seed source plus one
    unseeded read, and point the lint's module-level paths at it.

    Returns ``(pkg, svc)`` so a test can add files or rewrite a seed source.
    """
    pkg = tmp_path / "src" / "cofounder_agent" / "poindexter"
    svc = pkg / "services"
    migrations = svc / "migrations"
    brain = pkg / "brain"
    migrations.mkdir(parents=True)
    brain.mkdir(parents=True)
    (svc / "settings_defaults.py").write_text(
        "from __future__ import annotations\n"
        "DEFAULTS: dict[str, str] = {\n"
        "    'seeded_key': 'fine',\n"
        "}\n"
        "METADATA: dict = {}\n",
        encoding="utf-8",
    )
    (migrations / "0000_baseline.seeds.sql").write_text(
        "INSERT INTO app_settings (key, value, category, description, is_secret, is_active) "
        "VALUES ('baseline_seeded_key', '1', 'general', 'x', false, true) "
        "ON CONFLICT (key) DO NOTHING;\n",
        encoding="utf-8",
    )
    (brain / "seed_app_settings.json").write_text(
        json.dumps({"_meta": {"tier": "free"}, "settings": [
            {"key": "brain_seeded_key", "value": "1", "category": "general", "description": "x"},
        ]}),
        encoding="utf-8",
    )
    (pkg / "widget_service.py").write_text(
        "def f(site_config):\n"
        "    a = site_config.get('seeded_key', 'x')\n"
        "    b = site_config.get('baseline_seeded_key', 'x')\n"
        "    c = site_config.get('brain_seeded_key', 'x')\n"
        "    d = site_config.get('totally_unseeded_key', 'y')\n"
        "    return a, b, c, d\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(LINT, "REPO", tmp_path)
    monkeypatch.setattr(LINT, "PKG", pkg)
    monkeypatch.setattr(LINT, "SVC", svc)
    monkeypatch.setattr(LINT, "DEFAULTS_PY", svc / "settings_defaults.py")
    monkeypatch.setattr(LINT, "BASELINE_SEEDS", migrations / "0000_baseline.seeds.sql")
    monkeypatch.setattr(LINT, "BRAIN_SEED", brain / "seed_app_settings.json")
    return pkg, svc


class TestFindPhantomReadsAgainstAFakeTree:
    """End-to-end: a seeded read passes, an unseeded read fails — the
    contract the task that created this lint asked to be pinned."""

    def test_seeded_reads_pass_unseeded_read_fails(self, tmp_path, monkeypatch):
        _use_fake_tree(tmp_path, monkeypatch)

        result, n_files = LINT.find_phantom_reads()

        # widget_service.py + settings_defaults.py itself (PKG scans its own
        # services/ subtree, same as the real poindexter/ tree does).
        assert n_files == 2
        rel = WIDGET_REL
        # Only the one truly-unseeded key survives -- all three seed sources
        # (DEFAULTS / baseline.seeds.sql / brain JSON) correctly clear their
        # own key, and none of them are mistaken for clearing the others'.
        assert result == {rel: ["totally_unseeded_key"]}

    def test_allowlisted_key_never_reported_even_when_unseeded(self, tmp_path, monkeypatch):
        pkg, _svc = _use_fake_tree(tmp_path, monkeypatch)
        (pkg / "extra_service.py").write_text(
            "def g(site_config):\n"
            "    return site_config.get('database_url', '')\n",
            encoding="utf-8",
        )

        result, _n_files = LINT.find_phantom_reads()

        rel = "src/cofounder_agent/poindexter/extra_service.py"
        assert rel not in result
        assert "database_url" in LINT.ALLOWLIST

    def test_secret_accessor_never_reported_even_when_unseeded(self, tmp_path, monkeypatch):
        pkg, _svc = _use_fake_tree(tmp_path, monkeypatch)
        (pkg / "secret_service.py").write_text(
            "async def h(site_config):\n"
            "    return await site_config.get_secret('some_new_third_party_api_key', '')\n",
            encoding="utf-8",
        )

        result, _n_files = LINT.find_phantom_reads()

        rel = "src/cofounder_agent/poindexter/secret_service.py"
        assert rel not in result


class TestRegressionDetection:
    def test_new_key_in_an_already_baselined_file_is_a_regression(self):
        current = {"a.py": ["existing_key", "new_key"]}
        baseline = {"a.py": ["existing_key"]}
        assert LINT.find_regressions(current, baseline) == [("a.py", "new_key")]

    def test_same_key_in_a_new_file_is_also_a_regression(self):
        # A second copy of an already-known-bad key in a NEW file counts —
        # the baseline is keyed per file (like bandit's), not per key, so a
        # copy-pasted mistake elsewhere cannot ride in for free.
        current = {"a.py": ["known_bad_key"], "b.py": ["known_bad_key"]}
        baseline = {"a.py": ["known_bad_key"]}
        assert LINT.find_regressions(current, baseline) == [("b.py", "known_bad_key")]

    def test_fully_baselined_tree_has_no_regressions(self):
        current = {"a.py": ["k1", "k2"]}
        baseline = {"a.py": ["k1", "k2"]}
        assert LINT.find_regressions(current, baseline) == []


class TestStaleBaseline:
    """A baselined read that no longer exists is slack in the ratchet at
    exactly the spot the read is most likely to come back."""

    def test_fixed_read_leaves_a_stale_entry(self):
        # The real incident: the fix (#4065) merged, the baseline still held it.
        current: dict[str, list[str]] = {}
        baseline = {"gpu_scheduler.py": ["electricity_rate_kwh_usd"]}
        assert LINT.find_stale_baseline(current, baseline) == [
            ("gpu_scheduler.py", "electricity_rate_kwh_usd"),
        ]

    def test_still_present_read_is_not_stale(self):
        current = {"a.py": ["k1"]}
        baseline = {"a.py": ["k1"]}
        assert LINT.find_stale_baseline(current, baseline) == []

    def test_read_moved_to_another_file_is_stale_at_the_old_file(self):
        # ...and a regression at the new one — both halves fire, because the
        # baseline is keyed per file.
        current = {"b.py": ["k1"]}
        baseline = {"a.py": ["k1"]}
        assert LINT.find_stale_baseline(current, baseline) == [("a.py", "k1")]
        assert LINT.find_regressions(current, baseline) == [("b.py", "k1")]


class TestStaleAllowlist:
    """An ALLOWLIST entry with nothing left to exempt would silently excuse a
    future, genuine phantom read of the same key."""

    ALLOW = {"legacy_alias_key": "reason"}

    def test_live_exemption_is_not_stale(self):
        by_file = {"a.py": {"legacy_alias_key"}}
        assert LINT.find_stale_allowlist(by_file, {}, set(), self.ALLOW) == []

    def test_key_no_longer_read_is_stale(self):
        (key, why), = LINT.find_stale_allowlist({}, {}, set(), self.ALLOW)
        assert key == "legacy_alias_key"
        assert "reads it any more" in why

    def test_key_now_seeded_is_stale(self):
        by_file = {"a.py": {"legacy_alias_key"}}
        (key, why), = LINT.find_stale_allowlist(by_file, {}, {"legacy_alias_key"}, self.ALLOW)
        assert key == "legacy_alias_key"
        assert "seeded" in why

    def test_key_now_read_via_get_secret_is_stale(self):
        # e.g. eia_api_key moving to .get_secret(): the structural secret
        # exemption covers it, so the ALLOWLIST line is dead weight.
        by_file = {"a.py": {"legacy_alias_key"}}
        via_secret = {"legacy_alias_key": True}
        (key, why), = LINT.find_stale_allowlist(by_file, via_secret, set(), self.ALLOW)
        assert key == "legacy_alias_key"
        assert "get_secret" in why


class TestMainFailsOnStaleEntries:
    """The stale checks are wired into main(), not just defined."""

    def test_stale_baseline_entry_fails_the_run(self, tmp_path, monkeypatch, capsys):
        _pkg, svc = _use_fake_tree(tmp_path, monkeypatch)
        # Seed the one unseeded read so the tree itself is clean, leaving the
        # stale baseline entry as the only thing that can fail the run.
        (svc / "settings_defaults.py").write_text(
            "DEFAULTS: dict[str, str] = {\n"
            "    'seeded_key': 'fine',\n"
            "    'totally_unseeded_key': 'now seeded',\n"
            "}\n",
            encoding="utf-8",
        )
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"gone.py": ["fixed_long_ago"]}), encoding="utf-8")
        monkeypatch.setattr(LINT, "BASELINE_PATH", baseline)
        monkeypatch.setattr(LINT, "ALLOWLIST", {})
        monkeypatch.setattr("sys.argv", ["settings_phantom_read_lint.py"])

        assert LINT.main() == 1
        out = capsys.readouterr().out
        assert "STALE BASELINE ENTRY" in out
        assert "fixed_long_ago" in out

    def test_stale_allowlist_entry_fails_the_run(self, tmp_path, monkeypatch, capsys):
        _pkg, svc = _use_fake_tree(tmp_path, monkeypatch)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps({WIDGET_REL: ["totally_unseeded_key"]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(LINT, "BASELINE_PATH", baseline)
        monkeypatch.setattr(LINT, "ALLOWLIST", {"key_nothing_reads": "reason"})
        monkeypatch.setattr("sys.argv", ["settings_phantom_read_lint.py"])

        assert LINT.main() == 1
        out = capsys.readouterr().out
        assert "STALE ALLOWLIST ENTRY" in out
        assert "key_nothing_reads" in out


class TestBaselineRatchetAgainstRealTree:
    def test_real_tree_matches_baseline_and_allowlist_exactly(self):
        """The committed baseline and ALLOWLIST must describe the live tree
        exactly: no phantom read outside them, and no entry in either that
        no longer matches a real read.

        The stale half is not decoration. This lint shipped with a baseline
        entry for electricity_rate_kwh_usd that #4065 had already removed,
        and the old version of this test claimed to catch that while only
        checking for regressions.
        """
        by_file, via_secret, n_files = LINT._scan_tree()
        assert n_files > 100, n_files  # sanity: the real poindexter/ tree was scanned
        seeded = LINT._seeded_keys()
        current = LINT._filter_phantoms(by_file, via_secret, seeded)
        baseline = LINT.load_baseline()
        assert LINT.find_regressions(current, baseline) == []
        assert LINT.find_stale_baseline(current, baseline) == []
        assert LINT.find_stale_allowlist(by_file, via_secret, seeded) == []

    def test_baseline_is_empty_after_burndown(self):
        """Zero grandfathered phantom reads is the intended steady state.

        Pinned so a new phantom read can't be quietly re-baselined back to
        green: grandfathering one now means editing this test too, in the
        same diff, where a reviewer sees it.
        """
        assert LINT.load_baseline() == {}
