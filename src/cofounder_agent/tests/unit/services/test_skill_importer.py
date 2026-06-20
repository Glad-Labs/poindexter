"""Unit tests for services/skill_importer.py (poindexter#529).

All tests run without a real DB or network connection.  asyncpg pools and
httpx clients are mocked via unittest.mock.

Test classes
------------
TestParseSkillMd        — frontmatter parsing + field validation
TestLicenseCheck        — allowed-license enforcement
TestImportSkill         — end-to-end import (disk write, collision, GitHub URL)
"""

from __future__ import annotations

import textwrap
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The module under test is loaded lazily in each test to keep imports
# isolated from each other.
from services.skill_importer import (
    SkillImportError,
    _convert_github_blob_url,
    _parse_frontmatter,
    _resolve_allowed_licenses,
    _validate_meta,
    import_skill,
    list_skills,
    remove_skill,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_VALID_SKILL_MD = textwrap.dedent(
    """\
    ---
    name: my-skill
    description: A test skill for unit tests.
    license: MIT
    metadata:
      category: test
      prompts:
        - key: my_skill.generate
          output_format: text
          description: Generates something.
    ---

    # My skill

    ## my_skill.generate

    ```text
    Generate something about {topic}.
    ```
    """
)


def _make_pool(rows=None):
    """Return a minimal asyncpg-pool mock that supports acquire() + execute/fetch."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    pool.close = AsyncMock()
    return pool, conn


# ---------------------------------------------------------------------------
# TestParseSkillMd
# ---------------------------------------------------------------------------


class TestParseSkillMd:
    @pytest.mark.unit
    def test_valid_frontmatter_parses_ok(self):
        meta, _ = _parse_frontmatter(_VALID_SKILL_MD)
        assert meta["name"] == "my-skill"
        assert meta["license"] == "MIT"
        assert len(meta["metadata"]["prompts"]) == 1

    @pytest.mark.unit
    def test_missing_name_raises(self):
        bad = _VALID_SKILL_MD.replace("name: my-skill\n", "")
        meta, _ = _parse_frontmatter(bad)
        with pytest.raises(SkillImportError, match="missing required field 'name'"):
            _validate_meta(meta)

    @pytest.mark.unit
    def test_missing_prompts_raises(self):
        # Build a skill with an explicitly empty prompts list
        bad = textwrap.dedent(
            """\
            ---
            name: my-skill
            description: A test skill.
            license: MIT
            metadata:
              category: test
              prompts: []
            ---

            # My skill
            """
        )
        meta, _ = _parse_frontmatter(bad)
        with pytest.raises(SkillImportError, match="non-empty list"):
            _validate_meta(meta)

    @pytest.mark.unit
    def test_slug_invalid_name_raises(self):
        bad = _VALID_SKILL_MD.replace("name: my-skill", "name: My Skill!")
        meta, _ = _parse_frontmatter(bad)
        with pytest.raises(SkillImportError, match="not slug-safe"):
            _validate_meta(meta)

    @pytest.mark.unit
    def test_no_frontmatter_delimiter_raises(self):
        with pytest.raises(SkillImportError, match="must start with"):
            _parse_frontmatter("# No frontmatter here\n\nJust body.\n")

    @pytest.mark.unit
    def test_unclosed_frontmatter_raises(self):
        with pytest.raises(SkillImportError, match="not closed"):
            _parse_frontmatter("---\nname: oops\n")


# ---------------------------------------------------------------------------
# TestLicenseCheck
# ---------------------------------------------------------------------------


class TestLicenseCheck:
    @pytest.mark.unit
    def test_mit_allowed(self):
        site_config = MagicMock()
        site_config.get.return_value = "MIT,Apache-2.0"
        allowed = _resolve_allowed_licenses(site_config)
        assert "MIT" in allowed

    @pytest.mark.unit
    def test_apache_allowed(self):
        allowed = _resolve_allowed_licenses(None)
        assert "Apache-2.0" in allowed

    @pytest.mark.unit
    def test_gpl_rejected(self):
        # GPL-3.0 is not in the default allow-list
        allowed = _resolve_allowed_licenses(None)
        assert "GPL-3.0" not in allowed

    @pytest.mark.unit
    def test_unknown_license_rejected(self):
        site_config = MagicMock()
        site_config.get.return_value = "MIT,Apache-2.0"
        allowed = _resolve_allowed_licenses(site_config)
        assert "Proprietary" not in allowed

    @pytest.mark.unit
    def test_site_config_none_returns_defaults(self):
        allowed = _resolve_allowed_licenses(None)
        assert set(allowed) == {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}

    @pytest.mark.unit
    def test_site_config_empty_value_falls_back_to_defaults(self):
        site_config = MagicMock()
        site_config.get.return_value = ""
        allowed = _resolve_allowed_licenses(site_config)
        assert "MIT" in allowed


# ---------------------------------------------------------------------------
# TestImportSkill
# ---------------------------------------------------------------------------


class TestImportSkill:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_local_file_imports_to_disk(self, tmp_path):
        """A valid local SKILL.md is written to skills/<pack>/<name>/SKILL.md."""
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(_VALID_SKILL_MD, encoding="utf-8")

        skills_root = tmp_path / "skills"

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            result = await import_skill(
                str(source_file),
                pack="test-pack",
                pool=None,
                site_config=None,
                force=False,
            )

        assert result["ok"] is True
        assert result["name"] == "my-skill"
        assert result["pack"] == "test-pack"
        assert result["license"] == "MIT"
        assert result["prompt_count"] == 1

        dest = skills_root / "test-pack" / "my-skill" / "SKILL.md"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == _VALID_SKILL_MD

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_collision_without_force_raises(self, tmp_path):
        """Re-importing an existing skill without --force raises SkillImportError."""
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(_VALID_SKILL_MD, encoding="utf-8")

        skills_root = tmp_path / "skills"

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            # First install
            await import_skill(str(source_file), pack="test-pack", pool=None)

            # Second install without force
            with pytest.raises(SkillImportError, match="already installed"):
                await import_skill(
                    str(source_file),
                    pack="test-pack",
                    pool=None,
                    force=False,
                )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_collision_with_force_overwrites(self, tmp_path):
        """Re-importing with --force replaces the existing skill."""
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(_VALID_SKILL_MD, encoding="utf-8")

        skills_root = tmp_path / "skills"

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            await import_skill(str(source_file), pack="test-pack", pool=None)

            result = await import_skill(
                str(source_file),
                pack="test-pack",
                pool=None,
                force=True,
            )

        assert result["ok"] is True
        assert result["updated"] is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_url_github_blob_converted_to_raw(self, tmp_path):
        """GitHub blob URLs are converted to raw.githubusercontent.com before fetch."""
        skills_root = tmp_path / "skills"

        blob_url = (
            "https://github.com/some-org/some-repo/blob/main/skills/my-skill/SKILL.md"
        )
        expected_raw = (
            "https://raw.githubusercontent.com/some-org/some-repo/main/skills/my-skill/SKILL.md"
        )

        # Verify the URL conversion helper works
        assert _convert_github_blob_url(blob_url) == expected_raw

        # Mock httpx client
        mock_response = MagicMock()
        mock_response.content = _VALID_SKILL_MD.encode("utf-8")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await import_skill(
                    blob_url,
                    pack="imported",
                    pool=None,
                    site_config=None,
                )

        assert result["ok"] is True
        assert result["name"] == "my-skill"
        # Confirm get() was called with the raw URL (not the blob URL)
        call_url = mock_client.get.call_args[0][0]
        assert "raw.githubusercontent.com" in call_url
        assert "/blob/" not in call_url

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_license_not_allowed_raises(self, tmp_path):
        """A SKILL.md with a disallowed license raises SkillImportError."""
        gpl_skill = _VALID_SKILL_MD.replace("license: MIT", "license: GPL-3.0")
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(gpl_skill, encoding="utf-8")

        skills_root = tmp_path / "skills"

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            with pytest.raises(SkillImportError, match="GPL-3.0"):
                await import_skill(
                    str(source_file),
                    pack="imported",
                    pool=None,
                    site_config=None,
                )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_db_upsert_called_when_pool_provided(self, tmp_path):
        """When a pool is provided, _upsert_catalog is called."""
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(_VALID_SKILL_MD, encoding="utf-8")

        skills_root = tmp_path / "skills"
        pool, conn = _make_pool()

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            result = await import_skill(
                str(source_file),
                pack="test-pack",
                pool=pool,
                site_config=None,
            )

        assert result["ok"] is True
        # execute should have been called for the DB upsert
        conn.execute.assert_called()


# ---------------------------------------------------------------------------
# TestListAndRemoveSkills
# ---------------------------------------------------------------------------


class TestListAndRemoveSkills:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_skills_disk_mode(self, tmp_path):
        """list_skills(pool=None) reads from disk and parses each SKILL.md."""
        skills_root = tmp_path / "skills"
        skill_dir = skills_root / "imported" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(_VALID_SKILL_MD, encoding="utf-8")

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            result = await list_skills(pool=None)

        assert len(result) == 1
        assert result[0]["name"] == "my-skill"
        assert result[0]["pack"] == "imported"
        assert result[0]["license"] == "MIT"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_skills_db_mode(self):
        """list_skills(pool=pool) delegates to the DB and returns its rows."""
        pool, _ = _make_pool(
            rows=[
                {
                    "id": "abc123",
                    "name": "db-skill",
                    "pack": "imported",
                    "source_url": None,
                    "license": "MIT",
                    "description": "From DB.",
                    "import_hash": "deadbeef",
                    "prompt_count": 2,
                    "installed_at": None,
                    "updated_at": None,
                }
            ]
        )
        result = await list_skills(pool=pool)
        assert len(result) == 1
        assert result[0]["name"] == "db-skill"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_remove_skill_deletes_file(self, tmp_path):
        """remove_skill finds the SKILL.md by name and deletes it."""
        skills_root = tmp_path / "skills"
        skill_dir = skills_root / "imported" / "my-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(_VALID_SKILL_MD, encoding="utf-8")

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            result = await remove_skill("my-skill", pool=None)

        assert result["ok"] is True
        assert result["name"] == "my-skill"
        assert not skill_file.exists()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_remove_skill_not_found_raises(self, tmp_path):
        """remove_skill raises SkillImportError when the skill is not installed."""
        skills_root = tmp_path / "skills"
        skills_root.mkdir(parents=True)

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            with pytest.raises(SkillImportError, match="not installed"):
                await remove_skill("ghost-skill", pool=None)


# ---------------------------------------------------------------------------
# TestBodyValidation — import-time check that every declared key resolves
# ---------------------------------------------------------------------------


class TestBodyValidation:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_declared_key_without_body_section_raises(self, tmp_path):
        """A skill that DECLARES a prompt key but has no '## key' body section
        is rejected at import — not silently broken at runtime.

        Before this check the importer trusted frontmatter, wrote the file,
        and recorded a catalog row with prompt_count=N, while the runtime
        loader logged a warning and skipped the unresolvable key. The failure
        surfaced far from the import, at worker boot."""
        hollow = textwrap.dedent(
            """\
            ---
            name: hollow
            description: declares a key it never defines.
            license: MIT
            metadata:
              category: utility
              prompts:
                - key: hollow.go
            ---

            # Hollow skill

            (no '## hollow.go' section anywhere)
            """
        )
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(hollow, encoding="utf-8")
        skills_root = tmp_path / "skills"

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            with pytest.raises(SkillImportError, match="hollow.go"):
                await import_skill(str(source_file), pack="imported", pool=None)

        # Fail-loud BEFORE side effects: nothing written to disk.
        assert not (skills_root / "imported" / "hollow" / "SKILL.md").exists()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_all_declared_keys_present_imports_ok(self, tmp_path):
        """The happy path — every declared key has a matching section — still
        imports cleanly (guards against the validator being too strict)."""
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(_VALID_SKILL_MD, encoding="utf-8")
        skills_root = tmp_path / "skills"

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            result = await import_skill(str(source_file), pack="test-pack", pool=None)

        assert result["ok"] is True
        assert result["prompt_count"] == 1


# ---------------------------------------------------------------------------
# TestImportTelemetry — installed vs updated flags
# ---------------------------------------------------------------------------


class TestImportTelemetry:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_force_import_of_new_skill_reports_installed_not_updated(
        self, tmp_path
    ):
        """force=True on a skill that does NOT exist yet is a fresh install,
        not an update.

        Regression: ``was_existing`` was computed AFTER the file write, so the
        destination always 'existed' and a fresh force-import mislabeled itself
        ``updated=True`` / ``installed=False``."""
        source_file = tmp_path / "SKILL.md"
        source_file.write_text(_VALID_SKILL_MD, encoding="utf-8")
        skills_root = tmp_path / "skills"

        with patch("services.skill_importer._SKILLS_DIR", skills_root):
            result = await import_skill(
                str(source_file), pack="test-pack", pool=None, force=True,
            )

        assert result["installed"] is True
        assert result["updated"] is False
