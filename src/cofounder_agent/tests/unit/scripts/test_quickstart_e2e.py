"""The quick start is one sequence, written twice and run once.

README.md's ``## Quick start`` block is what a stranger pastes, and what
scripts/ci/quickstart_e2e.py executes verbatim in the ``quickstart-e2e``
workflow. docs/quickstart.mdx is the same sequence as a walkthrough. The two
drifted before (the Mintlify page never pulled the critic model the pipeline
hard-gates on), so these tests pin them together, and pin the driver's three
substitutions (the clone, the model pulls, and one stand-in settings row) so
the CI job keeps running the README rather than a paraphrase.
"""

from __future__ import annotations

import json
import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

from tests.unit._nonempty import nonempty

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "start-stack.sh").is_file()
)
README = _REPO_ROOT / "README.md"
QUICKSTART_MDX = _REPO_ROOT / "docs" / "quickstart.mdx"
TROUBLESHOOTING = _REPO_ROOT / "docs" / "operations" / "troubleshooting.md"
WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "quickstart-e2e.yml"
_BACKEND = _REPO_ROOT / "src" / "cofounder_agent" / "poindexter"
BASELINE_SCHEMA = _BACKEND / "services" / "migrations" / "0000_baseline.schema.sql"
BASELINE_SEEDS = _BACKEND / "services" / "migrations" / "0000_baseline.seeds.sql"
POST_PIPELINE_ACTIONS = _BACKEND / "services" / "post_pipeline_actions.py"
TOPIC_LENGTH = _BACKEND / "services" / "topic_length.py"
BLOG_TASK_CREATION = _BACKEND / "services" / "blog_task_creation.py"
AI_CONTENT_GENERATOR = _BACKEND / "modules" / "content" / "ai_content_generator.py"
TWO_PASS_WRITER = _BACKEND / "modules" / "content" / "atoms" / "two_pass_writer.py"


def _load_driver():
    path = _REPO_ROOT / "scripts" / "ci" / "quickstart_e2e.py"
    spec = spec_from_file_location("quickstart_e2e_driver", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DRIVER = _load_driver()


@pytest.fixture(scope="module")
def quick_start():
    return DRIVER.extract_quick_start(README.read_text(encoding="utf-8"))


def _commands(qs) -> list[str]:
    return [ln.strip() for ln in qs.lines if ln.strip() and not ln.strip().startswith("#")]


class TestReadmeQuickStart:
    def test_has_the_steps_that_make_a_post(self, quick_start):
        cmds = _commands(quick_start)
        for needed in (
            "pip install -e src/cofounder_agent",
            "poindexter setup --auto",
            "bash scripts/start-stack.sh up -d",
        ):
            assert needed in cmds, f"quick start lost {needed!r}: {cmds}"
        assert any(c.startswith("poindexter tasks create ") for c in cmds)
        # Setup before the stack (it provisions the stack's Postgres), and the
        # stack before the task.
        order = [
            next(i for i, c in enumerate(cmds) if c == "poindexter setup --auto"),
            next(i for i, c in enumerate(cmds) if c == "bash scripts/start-stack.sh up -d"),
            next(i for i, c in enumerate(cmds) if c.startswith("poindexter tasks create ")),
        ]
        assert order == sorted(order)

    def test_the_first_post_names_a_niche(self, quick_start):
        """The first post must be publishable, so its task must name a niche.

        A task with no niche generates and approves, but the #729 allowlist
        then refuses to stage it, so no post ever reaches the site. The
        seeded niche is the one a fresh install has.
        """
        (create,) = [
            c for c in _commands(quick_start) if c.startswith("poindexter tasks create ")
        ]
        assert "--niche starter-blog" in create, create
        seeds = BASELINE_SEEDS.read_text(encoding="utf-8")
        assert re.search(r"INSERT INTO niches \([^)]*\) VALUES \('[0-9a-f-]+', 'starter-blog'", seeds)

    def test_the_walkthrough_and_the_setup_guide_name_it_too(self):
        for path in (QUICKSTART_MDX, _REPO_ROOT / "docs" / "operations" / "local-development-setup.md"):
            text = path.read_text(encoding="utf-8")
            line = next(
                ln for ln in text.splitlines() if ln.strip().startswith("poindexter tasks create ")
            )
            assert "--niche starter-blog" in line, f"{path.name}: {line}"

    def test_the_loop_ends_at_the_local_site(self):
        """The README says to publish and where to read it, and the job reads it there.

        The port and path the driver fetches are the ones the README links and the
        consumer stack publishes, so a change to any one fails here rather than in
        a 40-minute CI run.
        """
        readme = README.read_text(encoding="utf-8")
        assert "poindexter tasks publish <id>" in readme
        assert "(http://localhost:8002/site/)" in readme
        assert DRIVER.SITE_URL == "http://localhost:8002/site"
        compose = (_REPO_ROOT / "docker-compose.consumer.yml").read_text(encoding="utf-8")
        assert '"8002:8002"' in compose

    def test_the_walkthrough_says_the_same(self):
        text = QUICKSTART_MDX.read_text(encoding="utf-8")
        assert "poindexter tasks publish <task_id>" in text
        assert "(http://localhost:8002/site/)" in text

    def test_pulls_the_embedding_model(self, quick_start):
        assert "nomic-embed-text" in quick_start.pulled_models

    def test_python_prerequisite_matches_the_package(self):
        """requires-python is >=3.13,<3.14; the README once said '3.13+'."""
        pyproject = (_REPO_ROOT / "src" / "cofounder_agent" / "pyproject.toml").read_text(encoding="utf-8")
        assert 'requires-python = ">=3.13,<3.14"' in pyproject
        text = README.read_text(encoding="utf-8")
        assert "3.13+" not in text.split("## Quick start", 1)[1].split("\n## ", 1)[0]

    def test_linux_ollama_bind_is_documented_and_exercised(self):
        """Containers reach host Ollama via the bridge gateway; loopback refuses it."""
        override = 'Environment="OLLAMA_HOST=0.0.0.0"'
        assert override in README.read_text(encoding="utf-8")
        assert override in QUICKSTART_MDX.read_text(encoding="utf-8")
        assert override in WORKFLOW.read_text(encoding="utf-8")


class TestMintlifyMatchesReadme:
    def test_same_models(self, quick_start):
        mdx_models = DRIVER._PULL.findall(QUICKSTART_MDX.read_text(encoding="utf-8"))
        assert sorted(set(mdx_models)) == sorted(set(quick_start.pulled_models))

    def test_every_readme_command_is_in_the_walkthrough(self, quick_start):
        mdx = QUICKSTART_MDX.read_text(encoding="utf-8")
        missing = [
            c for c in _commands(quick_start)
            if not DRIVER._PULL.search(c) and c not in mdx
        ]
        assert not missing, f"docs/quickstart.mdx is missing README steps: {missing}"


class TestDriver:
    def test_substitutes_only_the_clone_the_pulls_and_the_stand_in_settings(
        self, quick_start, tmp_path,
    ):
        script = DRIVER.build_script(
            quick_start, tree=tmp_path / "tree", tiny_model="tiny:1b",
            real_pulls={"nomic-embed-text"}, handoff=tmp_path / "h.env",
        )
        assert quick_start.clone_line not in script
        assert f"cd {tmp_path / 'tree'}" in script
        for tag in quick_start.pulled_models:
            if tag == "nomic-embed-text":
                assert "ollama pull nomic-embed-text" in script
            else:
                assert f"ollama cp tiny:1b {tag}" in script
        assert "ollama pull gemma" not in script
        # Everything else runs verbatim.
        for cmd in _commands(quick_start):
            if cmd != quick_start.clone_line and not DRIVER._PULL.search(cmd):
                assert cmd in script
        assert script.startswith("set -euo pipefail")
        # ... and the ONLY lines the README does not contain are the driver's:
        # its announcements, the clone's `cd`, the model aliases, the stand-in
        # settings and the hand-off. No other command may ride in.
        allowed = [
            r"set -euo pipefail",
            r"exec > >\(tee -a \S+\) 2>&1",
            r"echo '\[quickstart-e2e\] .*'",
            r"cd \S+",
            r"ollama (?:pull|cp) \S+(?: \S+)?",
            r"poindexter settings set \S+ \S+",
            r'echo "PDX_BIN=\$\(command -v poindexter\)" >> \S+',
        ]
        readme_lines = set(quick_start.lines)
        foreign = [
            ln for ln in script.splitlines()
            if ln.strip() and ln not in readme_lines
            and not any(re.fullmatch(pat, ln.strip()) for pat in allowed)
        ]
        assert foreign == [], f"the driver injected commands it does not declare: {foreign}"

    @pytest.mark.parametrize(
        ("readme", "match"),
        [
            ("# Title\n\nno quick start here\n", "no '## Quick start'"),
            ("## Quick start\n\nprose only\n## Next\n", "no ```bash block"),
            ("## Quick start\n```bash\nollama pull a:1\n```\n", "git clone"),
            ("## Quick start\n```bash\ngit clone https://x/y.git && cd y\n```\n", "pulls no models"),
            (
                "## Quick start\n```bash\ngit clone https://x/y.git && cd y\nollama pull a:1\n```\n",
                "tasks create",
            ),
        ],
    )
    def test_a_restructured_readme_fails_loudly(self, readme, match):
        with pytest.raises(ValueError, match=match):
            DRIVER.extract_quick_start(readme)


def _table_columns(table: str) -> set[str]:
    """Column names of ``table`` in the baseline schema (the shape production has)."""
    schema = BASELINE_SCHEMA.read_text(encoding="utf-8")
    match = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);", schema, re.S)
    assert match, f"{table} not in the baseline schema"
    cols = set()
    for line in match.group(1).splitlines():
        first = line.strip().split(" ", 1)[0].strip('"')
        if first and first.upper() not in {"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"}:
            cols.add(first)
    return cols


_SQL_WORDS = frozenset({
    "select", "from", "where", "order", "by", "desc", "asc", "limit", "in", "left", "text",
    "and", "or", "not", "null", "coalesce", "nullif",
})


def _column_problems(sql: str) -> list[str]:
    """Identifiers in a one-table SELECT that are not columns of that table."""
    table = re.search(r"\bFROM\s+(\w+)", sql, re.I).group(1)
    q = re.sub(r"'[^']*'", "", sql)                       # string literals, psql :'vars'
    q = re.sub(r"\bAS\s+\w+", "", q, flags=re.I)          # output aliases
    q = re.sub(r"\bFROM\s+\w+(?:\s+[a-z]\b)?", "", q, flags=re.I)  # the table and its alias
    q = re.sub(r"\b[a-z]\.", "", q)                      # alias qualifiers: a.timestamp
    words = {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", q) if w.lower() not in _SQL_WORDS}
    return sorted(words - _table_columns(table))


class TestStandInSettings:
    """The third substitution: what the stand-in model is excused from, and why."""

    def test_the_list_is_closed(self):
        """Each entry is a place the job stops measuring the README's own promise.

        Adding one takes a reason in the driver's docstring and an edit here, so
        a new accommodation is a decision, not a drive-by.
        """
        assert DRIVER.STAND_IN_SETTINGS == {
            "min_curation_score": "0",
            "content_gen_token_mult_standard": "2.5",
            "topic_discovery_length_distribution": "[[1000,1500,1.0]]",
        }

    def test_every_entry_is_a_seeded_row(self):
        """``settings set`` refuses a key that is not already a row, so a rename
        would otherwise surface as a failed run half an hour in. ``setup --auto``
        runs the baseline seeds AND ``seed_all_defaults``, so either makes the row."""
        from poindexter.services.settings_defaults import DEFAULTS

        seeds = BASELINE_SEEDS.read_text(encoding="utf-8")
        for key in nonempty(DRIVER.STAND_IN_SETTINGS, "STAND_IN_SETTINGS"):
            assert key in DEFAULTS or re.search(
                rf"INSERT INTO app_settings \([^)]*\) VALUES \('{re.escape(key)}',", seeds,
            ), f"{key} is neither a baseline-seeded row nor in settings_defaults.DEFAULTS"

    # Where each stand-in setting is read, as (file, needle). A new entry needs a
    # reader here, so the setting it excuses is shown to still do something.
    _READERS = {
        "min_curation_score": (POST_PIPELINE_ACTIONS, 'key="min_curation_score"'),
        "content_gen_token_mult_standard": (AI_CONTENT_GENERATOR, '"content_gen_token_mult_standard"'),
        "topic_discovery_length_distribution": (TOPIC_LENGTH, '"topic_discovery_length_distribution"'),
    }

    def test_each_setting_still_has_its_reader(self):
        for key in nonempty(DRIVER.STAND_IN_SETTINGS, "STAND_IN_SETTINGS"):
            assert key in self._READERS, f"no reader recorded for stand-in setting {key}"
            path, needle = self._READERS[key]
            assert needle in path.read_text(encoding="utf-8"), f"{path.name} no longer reads {key}"

    def test_the_two_pass_writer_takes_its_budget_from_that_reader(self):
        """The multiplier excuses the writer canonical_blog runs; it only does so
        while the two-pass writer's calls are capped through writer_max_tokens."""
        source = TWO_PASS_WRITER.read_text(encoding="utf-8")
        assert source.count("writer_max_tokens(") >= 2, "revise/expand are no longer capped"
        assert "max_tokens=writer_max_tokens(" in AI_CONTENT_GENERATOR.read_text(encoding="utf-8")

    def test_the_readme_task_takes_its_length_from_the_picker(self):
        """The length stand-in only bounds the draft while `tasks create` sends no
        --length (an explicit one wins over the picker) and the API picks it."""
        quick_start = DRIVER.extract_quick_start(README.read_text(encoding="utf-8"))
        assert "--length" not in quick_start.create_line
        assert "pick_target_length(site_config)" in BLOG_TASK_CREATION.read_text(encoding="utf-8")

    def test_the_stand_in_draft_budget_fits_its_timeout(self):
        """2.5 x the LONGEST target the stand-in length distribution can pick must
        fit the request timeout at the stand-in's measured CPU speed, and still
        clear a finished stand-in draft.

        Measured on the hosted runner (2026-10-19, qwen2.5:0.5b): ~13 tokens/s
        decode, ~87 tokens/s prefill, a finished draft ~2,000 tokens in ~2.5 min.
        Without the length stand-in the picker reached 3,500 words, and a capped
        draft still ran 7,462 tokens into the 600 s timeout (2026-10-20).
        The README's writer pin must take the STANDARD multiple, i.e. not be a
        thinking model under the seeded substrings, or the 12x one applies.
        """
        from poindexter.modules.content.ai_content_generator import writer_max_tokens
        from poindexter.services.site_config import SiteConfig
        from poindexter.services.topic_length import resolve_length_weights

        seeds = BASELINE_SEEDS.read_text(encoding="utf-8")

        def seeded(key: str) -> str:
            match = re.search(rf"VALUES \('{re.escape(key)}', '([^']*)'", seeds)
            assert match, f"{key} is no longer seeded by the baseline"
            return match.group(1)

        config = {
            key: seeded(key)
            for key in ("pipeline_writer_model", "thinking_model_substrings",
                        "niche_ollama_chat_timeout_seconds")
        }
        config.update(DRIVER.STAND_IN_SETTINGS)
        sc = SiteConfig(initial_config=config)
        buckets = resolve_length_weights(sc)
        assert buckets != resolve_length_weights(None), "the length stand-in did not parse"
        longest_target_words = max(hi for _lo, hi, _w in buckets)
        cap = writer_max_tokens(
            sc, target_length=longest_target_words, model=config["pipeline_writer_model"],
        )
        assert cap == int(longest_target_words * float(DRIVER.STAND_IN_SETTINGS[
            "content_gen_token_mult_standard"
        ])), "the README writer pin is classed as a thinking model; the 12x multiple applies"
        decode_tps, prefill_tps, prompt_tokens, finished_draft_tokens = 13.0, 87.0, 3000, 2000
        worst_case_s = prompt_tokens / prefill_tps + cap / decode_tps
        assert worst_case_s < 0.75 * float(config["niche_ollama_chat_timeout_seconds"])
        assert cap >= 1.5 * finished_draft_tokens

    def test_the_docs_state_the_bar_setup_seeds(self):
        """A stranger whose task ends `rejected` is told the bar; it must be the one
        `poindexter setup` seeds (the quick start's path), not a number that drifted."""
        seeds = BASELINE_SEEDS.read_text(encoding="utf-8")
        match = re.search(r"VALUES \('min_curation_score', '(\d+)'", seeds)
        assert match, "min_curation_score is no longer seeded by the baseline"
        for path in (README, QUICKSTART_MDX, TROUBLESHOOTING):
            text = path.read_text(encoding="utf-8")
            assert f"seeded at {match.group(1)}" in text, (
                f"{path.name} does not state the seeded curation bar ({match.group(1)})"
            )

    def test_applied_once_before_the_stack_starts(self, quick_start, tmp_path):
        """Right after `setup --auto` (the database exists) and before the stack
        boots, so every container loads them at startup. Written later, a value
        the worker reads from its cached settings (the length picker, at `tasks
        create`) races the cache's once-a-minute reload."""
        script = DRIVER.build_script(
            quick_start, tree=tmp_path, tiny_model="t:1", real_pulls=set(), handoff=tmp_path / "h.env",
        ).splitlines()
        setters = [i for i, ln in enumerate(script) if ln.startswith("poindexter settings set ")]
        assert len(setters) == len(DRIVER.STAND_IN_SETTINGS)
        setup = next(i for i, ln in enumerate(script) if ln.strip() == quick_start.setup_line)
        assert quick_start.setup_line == "poindexter setup --auto"
        # The echo, then the setters, directly under the setup line.
        assert min(setters) == setup + 2 and max(setters) == setup + 1 + len(setters)
        start_stack = next(i for i, ln in enumerate(script) if "start-stack.sh" in ln and "up" in ln)
        assert max(setters) < start_stack

    def test_json_values_survive_the_shell(self, quick_start, tmp_path):
        import shlex

        script = DRIVER.build_script(
            quick_start, tree=tmp_path, tiny_model="t:1", real_pulls=set(), handoff=tmp_path / "h.env",
        ).splitlines()
        written = {}
        for ln in script:
            if ln.startswith("poindexter settings set "):
                _p, _s, _set, key, value = shlex.split(ln)
                written[key] = value
        assert written == DRIVER.STAND_IN_SETTINGS

    def test_no_settings_means_a_verbatim_block(self, quick_start, tmp_path):
        script = DRIVER.build_script(
            quick_start, tree=tmp_path, tiny_model="t:1", real_pulls=set(),
            handoff=tmp_path / "h.env", settings={},
        )
        assert "settings set" not in script


class TestExplainTask:
    """A failed run must say WHY in its first line, not two containers' logs away."""

    def test_reports_the_curators_verdict(self):
        answers = iter([
            "evaluate_auto_publish 100%",
            "auto_curator rejected: Quality score 54.0 below threshold 75.0",
        ])
        why = DRIVER.explain_task("t-1", query=lambda sql, **kw: next(answers))
        assert why == (
            "last stage evaluate_auto_publish 100%; "
            "auto_curator rejected: Quality score 54.0 below threshold 75.0"
        )

    def test_an_unreadable_history_is_reported_not_raised(self):
        def boom(sql, **kw):
            raise RuntimeError("psql failed: container is not running")

        assert "could not read the task's history" in DRIVER.explain_task("t-1", query=boom)

    def test_a_task_with_no_history_explains_nothing(self):
        assert DRIVER.explain_task("t-1", query=lambda sql, **kw: "") == ""

    def test_its_queries_name_real_columns(self):
        """Run against production's schema by hand once (2026-09-28); this keeps
        them honest afterwards, against the baseline the same schema comes from."""
        seen: list[str] = []

        def record(sql, **kw):
            seen.append(sql)
            return ""

        DRIVER.explain_task("t-1", query=record)
        assert len(seen) == 2
        for sql in nonempty(seen, "explain_task queries"):
            assert _column_problems(sql) == [], sql


class TestStagedPosts:
    """The approve step must leave a `posts` row, not just an `approved` task."""

    def test_lists_each_staged_post(self):
        seen = {}

        def query(sql, **kw):
            seen.update(kw)
            return "why-docker-changed-everything (approved)\n"

        assert DRIVER.staged_posts("t-1", query=query) == ["why-docker-changed-everything (approved)"]
        assert seen == {"task_id": "t-1"}

    def test_no_row_is_an_empty_list(self):
        assert DRIVER.staged_posts("t-1", query=lambda sql, **kw: "") == []

    def test_an_unreadable_database_raises_rather_than_reading_as_no_post(self):
        def boom(sql, **kw):
            raise RuntimeError("psql failed")

        with pytest.raises(RuntimeError):
            DRIVER.staged_posts("t-1", query=boom)

    def test_its_query_names_real_columns(self):
        seen: list[str] = []

        def record(sql, **kw):
            seen.append(sql)
            return ""

        DRIVER.staged_posts("t-1", query=record)
        for sql in nonempty(seen, "staged_posts query"):
            assert _column_problems(sql) == [], sql

    def test_the_seam_key_is_the_one_publishing_writes(self):
        """`pipeline_task_id` in posts.metadata is what publish_service stamps."""
        source = (_BACKEND / "services" / "publish_service.py").read_text(encoding="utf-8")
        assert "'pipeline_task_id'" in source or '"pipeline_task_id"' in source


class TestJudgeApprove:
    """What makes the README's last instruction count as having worked."""

    TASK = "0123456789abcdef"

    def _judge(self, *, exit_code=0, stderr="", status="approved", rows="a-post (approved)"):
        return DRIVER.judge_approve(
            self.TASK, exit_code=exit_code, stderr=stderr, status_after=status,
            query=lambda sql, **kw: rows,
        )

    def test_approved_with_a_staged_post_is_sound(self):
        assert self._judge() is None
        assert self._judge(status="published") is None

    def test_approved_but_nothing_staged_fails_with_the_clis_own_warning(self):
        """The first-hour bug: status reads `approved`, no post exists."""
        said = "Warning: Approved, but no post was staged - niche_slug is not set"
        problem = self._judge(rows="", stderr=f"noise\n{said}\n")
        assert problem is not None
        assert "reported success but staged no post" in problem
        assert said in problem

    def test_nothing_staged_and_no_warning_still_fails_and_says_so(self):
        problem = self._judge(rows="")
        assert problem is not None and "the CLI printed no warning" in problem

    def test_a_failed_approve_is_reported_before_the_post_check(self):
        problem = self._judge(exit_code=1, status="awaiting_approval", rows="")
        assert problem is not None
        assert "left the task 'awaiting_approval' (exit 1)" in problem
        assert "staged no post" not in problem

    def test_an_unreadable_database_is_a_problem_not_a_pass(self):
        def boom(sql, **kw):
            raise RuntimeError("psql failed")

        problem = DRIVER.judge_approve(
            self.TASK, exit_code=0, stderr="", status_after="approved", query=boom,
        )
        assert problem is not None and "could not look" in problem

    def test_main_uses_it(self):
        """main() drives docker and cannot run here; pin that it defers to the judge."""
        source = (_REPO_ROOT / "scripts" / "ci" / "quickstart_e2e.py").read_text(encoding="utf-8")
        assert "judge_approve(" in source.split("def main(")[1]


class TestPublishedPost:
    def test_reads_slug_status_and_published_at(self):
        row = "why-docker-0123abcd|published|2026-09-29 12:00:00+00"
        assert DRIVER.published_post("t-1", query=lambda sql, **kw: row) == (
            "why-docker-0123abcd", "published", "2026-09-29 12:00:00+00",
        )

    def test_a_null_published_at_is_an_empty_string(self):
        assert DRIVER.published_post("t-1", query=lambda sql, **kw: "a|approved|") == ("a", "approved", "")

    def test_no_row_is_none(self):
        assert DRIVER.published_post("t-1", query=lambda sql, **kw: "") is None

    def test_its_query_names_real_columns(self):
        seen: list[str] = []

        def record(sql, **kw):
            seen.append(sql)
            return ""

        DRIVER.published_post("t-1", query=record)
        for sql in nonempty(seen, "published_post query"):
            assert _column_problems(sql) == [], sql


class TestJudgePublish:
    TASK = "0123456789abcdef"

    def _judge(self, *, exit_code=0, stderr="", post="a-slug|published|2026-09-29 12:00:00+00", task="published"):
        def query(sql, **kw):
            return post if "FROM posts" in sql else task

        return DRIVER.judge_publish(self.TASK, exit_code=exit_code, stderr=stderr, query=query)

    def test_a_published_post_and_task_is_sound_and_returns_the_slug(self):
        assert self._judge() == (None, "a-slug")

    def test_a_failed_publish_names_the_clis_last_line_and_has_no_slug(self):
        problem, slug = self._judge(exit_code=1, stderr="noise\nError: Publish failed: niche")
        assert slug is None
        assert "exited 1" in problem and "Error: Publish failed: niche" in problem

    def test_a_post_still_approved_is_a_problem_but_keeps_the_slug(self):
        problem, slug = self._judge(post="a-slug|approved|")
        assert slug == "a-slug"
        assert "'approved'" in problem and "published_at='NULL'" in problem

    def test_a_published_post_with_no_published_at_is_a_problem(self):
        problem, _ = self._judge(post="a-slug|published|")
        assert problem is not None and "published_at='NULL'" in problem

    def test_a_task_that_did_not_follow_the_post_is_a_problem(self):
        problem, slug = self._judge(task="approved")
        assert slug == "a-slug"
        assert "its task reads 'approved'" in problem

    def test_no_post_at_all_is_a_problem(self):
        problem, slug = self._judge(post="")
        assert slug is None and "no post" in problem

    def test_an_unreadable_database_is_a_problem_not_a_pass(self):
        def boom(sql, **kw):
            raise RuntimeError("psql failed")

        problem, slug = DRIVER.judge_publish(self.TASK, exit_code=0, stderr="", query=boom)
        assert slug is None and "could not read back" in problem


_CSP = "default-src 'self'; script-src 'self'; object-src 'none'"
_VIEWER_HTML = '<html><script src="/site/_viewer/viewer.js" defer></script></html>'


def _site(slug="a-slug", **overrides):
    """A healthy `/site/`, as `fetch(url) -> (status, headers, body)`; overrides are path -> response."""
    html = {"content-type": "text/html; charset=utf-8", "content-security-policy": _CSP}
    js = {"content-type": "application/json"}
    responses = {
        f"static/posts/{slug}.json": (200, js, json.dumps({"slug": slug, "title": "T", "content": "<p>x</p>"})),
        "static/posts/index.json": (200, js, json.dumps({"posts": [{"slug": slug}], "total": 1})),
        "": (200, html, _VIEWER_HTML),
        f"posts/{slug}": (200, html, _VIEWER_HTML),
        "_viewer/viewer.js": (200, {"content-type": "text/javascript"}, "//js"),
        "_viewer/viewer.css": (200, {"content-type": "text/css"}, "/*css*/"),
    }
    responses.update(overrides)

    def fetch(url):
        path = url.removeprefix(DRIVER.SITE_URL + "/")
        if path not in responses:
            return 404, {}, "not found"
        got = responses[path]
        if isinstance(got, Exception):
            raise got
        return got

    return fetch


class TestJudgeSite:
    """What a reader's browser would get from the published post, over HTTP."""

    def test_a_healthy_site_has_no_problems(self):
        assert DRIVER.judge_site("a-slug", fetch=_site()) == []

    def test_it_fetches_every_surface_a_reader_touches(self):
        seen = []
        healthy = _site()

        def spy(url):
            seen.append(url.removeprefix(DRIVER.SITE_URL + "/"))
            return healthy(url)

        DRIVER.judge_site("a-slug", fetch=spy)
        assert sorted(seen) == sorted([
            "static/posts/a-slug.json", "static/posts/index.json", "", "posts/a-slug",
            "_viewer/viewer.js", "_viewer/viewer.css",
        ])

    def test_a_missing_post_file_is_a_problem(self):
        problems = DRIVER.judge_site("a-slug", fetch=_site(**{"static/posts/a-slug.json": (404, {}, "")}))
        assert problems == [f"{DRIVER.SITE_URL}/static/posts/a-slug.json: HTTP 404"]

    def test_a_post_with_no_body_is_a_problem(self):
        body = json.dumps({"slug": "a-slug", "title": "T", "content": "  "})
        problems = DRIVER.judge_site(
            "a-slug", fetch=_site(**{"static/posts/a-slug.json": (200, {"content-type": "application/json"}, body)}),
        )
        assert problems == ["static/posts/a-slug.json has no content"]

    def test_a_post_file_carrying_another_slug_is_a_problem(self):
        body = json.dumps({"slug": "other", "title": "T", "content": "x"})
        problems = DRIVER.judge_site(
            "a-slug", fetch=_site(**{"static/posts/a-slug.json": (200, {"content-type": "application/json"}, body)}),
        )
        assert any("carries slug 'other'" in p for p in problems)

    def test_an_index_that_does_not_list_the_post_is_a_problem(self):
        body = json.dumps({"posts": [{"slug": "someone-else"}], "total": 1})
        problems = DRIVER.judge_site(
            "a-slug", fetch=_site(**{"static/posts/index.json": (200, {"content-type": "application/json"}, body)}),
        )
        assert len(problems) == 1 and "does not list 'a-slug'" in problems[0]

    def test_html_where_json_was_expected_is_a_problem(self):
        html = {"content-type": "text/html"}
        problems = DRIVER.judge_site("a-slug", fetch=_site(**{"static/posts/index.json": (200, html, "<html>")}))
        assert len(problems) == 1 and "content-type" in problems[0]

    def test_a_viewer_page_without_the_csp_is_a_problem(self):
        bare = (200, {"content-type": "text/html"}, _VIEWER_HTML)
        problems = DRIVER.judge_site("a-slug", fetch=_site(**{"": bare}))
        assert len(problems) == 1 and "Content-Security-Policy" in problems[0]

    def test_a_viewer_page_that_loads_no_script_is_a_problem(self):
        page = (200, {"content-type": "text/html", "content-security-policy": _CSP}, "<html></html>")
        problems = DRIVER.judge_site("a-slug", fetch=_site(**{"posts/a-slug": page}))
        assert len(problems) == 1 and "does not load the viewer script" in problems[0]

    def test_a_missing_viewer_asset_is_a_problem(self):
        problems = DRIVER.judge_site("a-slug", fetch=_site(**{"_viewer/viewer.js": (404, {}, "")}))
        assert problems == [f"{DRIVER.SITE_URL}/_viewer/viewer.js: HTTP 404"]

    def test_an_unreachable_site_reports_each_surface_rather_than_raising(self):
        down = RuntimeError("connection refused")

        def fetch(url):
            raise down

        problems = DRIVER.judge_site("a-slug", fetch=fetch)
        assert len(problems) == 6 and set(problems) == {"connection refused"}


class TestStorageFailures:
    def test_finds_both_failure_lines(self):
        log = (
            "ok [STORAGE] Stored locally: http://x/a.json (0.0MB)\n"
            "warn [STORAGE] No object-store credentials in app_settings (storage_access_key)\n"
            "err  [STORAGE] Local write failed for static/posts/a.json under /data: denied\n"
        )
        found = DRIVER.storage_failures(log)
        assert len(found) == 2 and found[0].startswith("warn") and found[1].startswith("err")

    def test_a_healthy_log_finds_none(self):
        assert DRIVER.storage_failures("[STORAGE] Stored locally: http://x/a.json\n") == []

    def test_the_strings_match_what_the_storage_layer_logs(self):
        """A reworded log line would silently blind the check."""
        source = (_BACKEND / "services" / "r2_upload_service.py").read_text(encoding="utf-8")
        for needle in nonempty(DRIVER.STORAGE_FAILURES, "STORAGE_FAILURES"):
            tail = needle.removeprefix("[STORAGE] ")
            assert f'"[STORAGE] {tail}' in source, needle


class TestMainOrchestration:
    """main() end to end, with the stack, docker and HTTP replaced by fakes.

    It is the one place that decides the ORDER (approve, then publish, then the
    read-back) and what fails the run, and the pure judges above are worth
    nothing if main() forgets to call them.
    """

    TASK = "0123456789abcdef"
    SLUG = "why-docker-01234567"

    def _run(
        self, monkeypatch, tmp_path, capsys, *,
        approve_stderr="", staged="why-docker-01234567 (approved)", publish_rc=0,
        site=None, logs="", qa_rc=0,
    ):
        import subprocess as sp

        (tmp_path / "README.md").write_text(README.read_text(encoding="utf-8"), encoding="utf-8")
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir()
        calls: list[list[str]] = []
        state = {"published": False}
        fetched: list[str] = []

        def run(argv, **kw):
            calls.append(list(argv))
            if argv[0] == "bash":
                (handoff_dir / "handoff.log").write_text(f"Created: {self.TASK}\n")
                (handoff_dir / "handoff.env").write_text("PDX_BIN=pdx\n")
                return sp.CompletedProcess(argv, 0, "", "")
            if argv[1:3] == ["tasks", "list"]:
                return sp.CompletedProcess(argv, 0, f"{self.TASK[:8]} awaiting_approval\n", "")
            if argv[1:3] == ["tasks", "qa"]:
                return sp.CompletedProcess(argv, qa_rc, "QA PASSED  score 91.0 (needs 80)\n", "")
            if argv[1:3] == ["tasks", "approve"]:
                return sp.CompletedProcess(argv, 0, "Approved\n", approve_stderr)
            if argv[1:3] == ["tasks", "publish"]:
                state["published"] = publish_rc == 0
                return sp.CompletedProcess(argv, publish_rc, "", "Error: nope" if publish_rc else "")
            raise AssertionError(f"unexpected command {argv}")

        def psql(sql, **kw):
            if "FROM pipeline_tasks" in sql:
                return "published" if state["published"] else "approved"
            if "FROM posts" in sql and "published_at" in sql:
                return f"{self.SLUG}|published|2026-09-29 12:00:00+00" if staged else ""
            if "FROM posts" in sql:
                return staged
            return "null"

        healthy = site or _site(self.SLUG)

        def fetch(url):
            fetched.append(url)
            return healthy(url)

        monkeypatch.setattr(DRIVER.tempfile, "mkdtemp", lambda prefix="": str(handoff_dir))
        monkeypatch.setattr(DRIVER.subprocess, "run", run)
        monkeypatch.setattr(DRIVER, "_psql", psql)
        monkeypatch.setattr(DRIVER, "_fetch", fetch)
        monkeypatch.setattr(DRIVER, "wait_for_task", lambda *a, **kw: DRIVER.SUCCESS)
        monkeypatch.setattr(DRIVER, "check_links", lambda readme: [])
        monkeypatch.setattr(DRIVER, "_container_logs", lambda name: logs)

        rc = DRIVER.main(["--tree", str(tmp_path), "--tiny-model", "t:1"])
        out = capsys.readouterr().out
        verbs = [c[2] for c in calls if c[0] == "pdx" and c[1] == "tasks"]
        return rc, out, verbs, fetched

    def test_the_happy_path_approves_publishes_then_reads_the_post_back(self, monkeypatch, tmp_path, capsys):
        rc, out, verbs, fetched = self._run(monkeypatch, tmp_path, capsys)
        assert rc == 0, out
        assert verbs == ["list", "qa", "approve", "publish"]
        assert f"{DRIVER.SITE_URL}/static/posts/{self.SLUG}.json" in fetched
        assert f"read '{self.SLUG}' back from" in out

    def test_a_failed_qa_command_fails_the_run(self, monkeypatch, tmp_path, capsys):
        rc, out, verbs, _ = self._run(monkeypatch, tmp_path, capsys, qa_rc=1)
        assert rc == 1
        assert "`poindexter tasks qa 01234567` failed (exit 1)" in out
        assert verbs == ["list", "qa", "approve", "publish"], "the rest of the loop still runs"

    def test_a_site_that_cannot_serve_the_post_fails_the_run(self, monkeypatch, tmp_path, capsys):
        broken = _site(self.SLUG, **{"_viewer/viewer.js": (404, {}, "")})
        rc, out, _, _ = self._run(monkeypatch, tmp_path, capsys, site=broken)
        assert rc == 1
        assert "::error::" in out and "_viewer/viewer.js: HTTP 404" in out

    def test_a_storage_failure_in_the_logs_fails_a_run_whose_reads_pass(self, monkeypatch, tmp_path, capsys):
        rc, out, _, _ = self._run(
            monkeypatch, tmp_path, capsys,
            logs="[STORAGE] Local write failed for static/posts/x.json under /data: denied",
        )
        assert rc == 1
        assert "a pipeline container logged a storage failure" in out

    def test_a_failed_publish_fails_the_run_and_reads_nothing(self, monkeypatch, tmp_path, capsys):
        rc, out, verbs, fetched = self._run(monkeypatch, tmp_path, capsys, publish_rc=1)
        assert rc == 1
        assert verbs[-1] == "publish" and fetched == []
        assert "`poindexter tasks publish 01234567` exited 1" in out

    def test_an_approve_that_staged_nothing_stops_before_publishing(self, monkeypatch, tmp_path, capsys):
        rc, out, verbs, fetched = self._run(
            monkeypatch, tmp_path, capsys, staged="",
            approve_stderr="Warning: Approved, but no post was staged - niche_slug is not set",
        )
        assert rc == 1
        assert verbs == ["list", "qa", "approve"] and fetched == []
        assert "staged no post (Warning: Approved, but no post was staged" in out


class TestWorkflow:
    def test_diagnostics_queries_name_real_columns(self):
        """The failure diagnostics only run when the job has already failed, so a
        typo there hides exactly when it matters: the first version asked
        ``audit_log`` for ``created_at`` (the column is ``timestamp``) and printed
        an error where the rejection reason should have been."""
        wf = WORKFLOW.read_text(encoding="utf-8")
        queries = re.findall(
            r'"(SELECT [^"]*? FROM (?:audit_log|pipeline_gate_history|pipeline_tasks)\b[^"]*)"', wf,
        )
        assert len(queries) >= 3
        for sql in nonempty(queries, "diagnostics queries"):
            assert _column_problems(sql) == [], sql

    def test_runs_the_driver_on_a_hosted_runner(self):
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        job = wf["jobs"]["quickstart"]
        # Never the vars.CI_RUNNER seam: it starts a Docker stack.
        assert job["runs-on"] == "ubuntu-latest"
        runs = "\n".join(step.get("run", "") for step in job["steps"])
        assert "scripts/ci/quickstart_e2e.py" in runs
        # The tree under test is the mirror's, built by the real sync filter.
        assert "bash scripts/sync-to-github.sh" in runs


class TestMissingModels:
    """The E2E fails on a model the pipeline calls that the README never pulls."""

    def test_finds_ollama_not_found_errors_in_any_quoting(self):
        log = (
            'OllamaException - {"error":"model \\"qwen3-vl:30b-a3b-instruct\\" not found, try pulling it first"}\n'
            'error: model "gemma3:27b" not found, try pulling it first\n'
            "model 'gemma3:27b' not found, try pulling it first\n"
        )
        assert DRIVER.missing_models(log) == ["qwen3-vl:30b-a3b-instruct", "gemma3:27b"]

    def test_clean_logs_find_nothing(self):
        assert DRIVER.missing_models("[INFO] qa.critic scored 72") == []


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TestWaitForTask:
    def _wait(self, rows, **kw):
        clock = _FakeClock()
        it = iter(rows)
        last = [None]

        def read_row():
            last[0] = next(it, last[0])
            return last[0]

        return DRIVER.wait_for_task(
            "0123456789", read_row=read_row, clock=clock, sleep=clock.sleep, poll_s=60, **kw,
        )

    def test_success(self):
        assert self._wait(["pending||0", "in_progress|draft|10", "awaiting_approval|done|100"],
                          timeout_min=60) == "awaiting_approval"

    def test_terminal_failure_returns_immediately(self):
        assert self._wait(["pending||0", "failed|draft|10"], timeout_min=60) == "failed"

    def test_never_dispatched_fails_fast(self):
        assert self._wait(["pending||0"], timeout_min=150, dispatch_min=12).startswith("never dispatched")

    def test_stalled_run_fails_fast(self):
        out = self._wait(["pending||0", "in_progress|qa|40"], timeout_min=150, stall_min=45)
        assert out.startswith("stalled")

    def test_a_missing_row_is_reported_as_the_wrong_id_not_a_stall(self):
        out = self._wait([""], timeout_min=150, stall_min=45)
        assert "not in pipeline_tasks" in out

    def test_rejected_retry_is_a_regeneration_not_a_verdict(self):
        assert self._wait(
            ["pending||0", "rejected_retry|qa|50", "in_progress|draft|10", "awaiting_approval|done|100"],
            timeout_min=60,
        ) == "awaiting_approval"


_README_BASE = """# Poindexter

210 live posts, 20,000 tests.

## Quick start

```bash
git clone https://example.invalid/x.git && cd x
ollama pull a:1
poindexter tasks create "t"
```

## Documentation

See the docs.
"""


class TestGate:
    """The 75-minute run is only worth starting when its outcome can change.

    Runs the workflow's own ``decide`` step in throwaway git repos, so the
    script that ships is the script that is tested.
    """

    @staticmethod
    def _script() -> str:
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = wf["jobs"]["gate"]["steps"]
        return next(s["run"] for s in steps if s.get("id") == "decide")

    def _decide(self, tmp_path, *, event="pull_request", base=None, head=None) -> str:
        import shutil
        import subprocess

        if shutil.which("git") is None or shutil.which("bash") is None:
            pytest.skip("needs git and bash")
        repo = tmp_path / "repo"
        repo.mkdir()
        env = {"PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path),
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}

        def git(*args) -> str:
            return subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                cwd=repo, env=env, check=True, capture_output=True, text=True,
            ).stdout.strip()

        def write(files):
            for rel, text in files.items():
                path = repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

        git("init", "-q", "-b", "main")
        write({"README.md": _README_BASE, "docs/quickstart.mdx": "walkthrough\n", **(base or {})})
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        base_sha = git("rev-parse", "HEAD")
        write(head or {})
        git("add", "-A")
        git("commit", "-q", "--allow-empty", "-m", "head")
        head_sha = git("rev-parse", "HEAD")

        out = tmp_path / "gh_output"
        proc = subprocess.run(
            ["bash", "-c", self._script()],
            cwd=repo, capture_output=True, text=True, check=False,
            env={**env, "EVENT": event, "BASE_SHA": base_sha, "HEAD_SHA": head_sha,
                 "GITHUB_OUTPUT": str(out)},
        )
        assert proc.returncode == 0, proc.stderr
        return out.read_text().strip()

    @pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
    def test_weekly_and_manual_runs_always_prove_it(self, tmp_path, event):
        assert self._decide(tmp_path, event=event) == "run=true"

    def test_a_stats_sync_edit_outside_the_quick_start_costs_nothing(self, tmp_path):
        readme = _README_BASE.replace("210 live posts", "211 live posts")
        assert self._decide(tmp_path, head={"README.md": readme}) == "run=false"

    def test_a_quick_start_edit_runs_it(self, tmp_path):
        readme = _README_BASE.replace("ollama pull a:1", "ollama pull b:2")
        assert self._decide(tmp_path, head={"README.md": readme}) == "run=true"

    def test_the_walkthrough_alone_is_not_executed(self, tmp_path):
        """docs/quickstart.mdx is held to the README by a unit test, not by E2E."""
        assert self._decide(tmp_path, head={"docs/quickstart.mdx": "changed\n"}) == "run=false"

    @pytest.mark.parametrize(
        "path",
        ["docker-compose.consumer.yml", "src/cofounder_agent/poindexter/cli/setup.py", "scripts/start-stack.sh"],
    )
    def test_changes_to_the_machinery_always_run_it(self, tmp_path, path):
        assert self._decide(tmp_path, head={path: "x\n"}) == "run=true"

    def test_machinery_plus_a_stats_edit_still_runs(self, tmp_path):
        readme = _README_BASE.replace("210 live posts", "211 live posts")
        assert self._decide(
            tmp_path, head={"README.md": readme, "docker-compose.consumer.yml": "x\n"},
        ) == "run=true"

    @staticmethod
    def _trigger_paths() -> list[str]:
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        trigger = wf.get("on") or wf.get(True)  # PyYAML reads a bare `on:` as True
        return trigger["pull_request"]["paths"]

    def test_every_trigger_path_still_exists(self):
        """A renamed file would drop out of the filter without a word: the PR that
        broke the quick start would then skip the one job that runs it."""
        for path in nonempty(self._trigger_paths(), "quickstart-e2e trigger paths"):
            target = _REPO_ROOT / path.removesuffix("/**")
            assert target.exists(), f"{path} no longer exists; the filter would silently skip it"

    @pytest.mark.parametrize(
        "path",
        [
            "src/cofounder_agent/poindexter/routes/task_publishing_routes.py",
            "src/cofounder_agent/poindexter/services/blog_task_creation.py",
            "src/cofounder_agent/poindexter/services/publish_service.py",
            "src/cofounder_agent/poindexter/services/static_export_service.py",
            "src/cofounder_agent/poindexter/services/r2_upload_service.py",
            "src/cofounder_agent/poindexter/services/local_object_store.py",
            "src/cofounder_agent/poindexter/services/local_site.py",
            "src/cofounder_agent/poindexter/utils/local_site_mount.py",
            "src/cofounder_agent/poindexter/local_site_viewer/**",
        ],
    )
    def test_the_approve_publish_readback_path_triggers_the_job(self, path):
        assert path in self._trigger_paths()

    def test_the_run_job_waits_on_the_gate(self):
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        job = wf["jobs"]["quickstart"]
        assert job["needs"] == "gate"
        assert job["if"] == "needs.gate.outputs.run == 'true'"


class TestOptionalModels:
    _README = (
        "**Optional — pull one when you switch its feature on:**\n\n"
        "| Model | Size | Used for |\n| --- | --- | --- |\n"
        "| `a:1` | 1 GB | thing |\n| `b:2` | small | other |\n\nnext paragraph\n"
    )

    def test_reads_the_tags_from_the_table(self):
        assert DRIVER.optional_models(self._README) == ["a:1", "b:2"]

    def test_no_table_means_no_optional_models(self):
        assert DRIVER.optional_models("# nothing here\n") == []

    def test_the_real_readme_has_an_optional_table(self):
        assert len(DRIVER.optional_models(README.read_text(encoding="utf-8"))) >= 3


class TestRecording:
    """--record films each step for the setup-and-operation how-to (2026-10-18)."""

    def test_timing_becomes_an_asciicast_with_cumulative_times(self):
        out = b"Script started on 2026-10-18 [COMMAND=\"x\"]\nhello \xe2\x9c\x93 world\n"
        # The tick (3 bytes) is split across two writes; it must survive whole.
        timing = "H 0.0 START_TIME x\nO 0.5 7\nO 0.25 2\nO 1.0 9\nH 0.0 EXIT_CODE 0\n"
        cast = DRIVER.timing_to_asciicast(timing, out, title="Install")
        header, *events = [json.loads(ln) for ln in cast.splitlines()]
        assert header == {"version": 2, "width": DRIVER.RECORD_COLS, "height": DRIVER.RECORD_ROWS, "title": "Install"}
        assert [e[0] for e in events] == [0.5, 0.75, 1.75]
        assert "".join(e[2] for e in events) == "hello ✓ world\n"
        assert all(e[1] == "o" for e in events)

    def test_credentials_are_masked_at_their_own_length(self):
        printed = (
            b"  client_id:      pdx-cli\n  client_secret:  s3cr3t-value-123\n"
            b"  Postgres:   abcdef123456...\n  Grafana:    zyxwvu987654...\n"
        )
        masked = DRIVER.redact(printed)
        assert len(masked) == len(printed), "the timing log's byte offsets must stay true"
        assert b"s3cr3t" not in masked and b"abcdef" not in masked and b"zyxwvu" not in masked
        assert b"client_secret:  ****************\n" in masked
        assert b"client_id:      pdx-cli" in masked

    def test_a_secret_split_across_writes_is_still_masked(self):
        out = b"Script started on x\n  client_secret:  s3cr3t-value\n"
        timing = "O 0.1 20\nO 0.1 11\n"  # 31 bytes, split mid-secret
        cast = DRIVER.timing_to_asciicast(timing, out)
        text = "".join(json.loads(ln)[2] for ln in cast.splitlines()[1:])
        assert "s3cr3t" not in text
        assert text == "  client_secret:  ************\n"

    def test_unrecorded_steps_run_exactly_as_before(self, monkeypatch, tmp_path):
        seen = []
        monkeypatch.setattr(DRIVER.subprocess, "run", lambda argv, **kw: seen.append(argv) or DRIVER.subprocess.CompletedProcess(argv, 0, "", ""))
        rec = DRIVER.Recorder(None)
        rec.run("install", "t", "echo hi", cwd=tmp_path, capture=False)
        rec.run("list", "t", ["pdx", "tasks", "list"], cwd=tmp_path, capture=True)
        assert seen == [["bash", "-c", "echo hi"], ["pdx", "tasks", "list"]]
        assert not list(tmp_path.iterdir())

    @pytest.mark.skipif(sys.platform != "linux", reason="util-linux script")
    def test_a_recorded_step_writes_a_cast_and_the_manifest(self, tmp_path):
        if not DRIVER.script_supports_recording():
            pytest.skip("no util-linux script 2.35+ with a pty here (the hosted runner that records has one)")
        rec = DRIVER.Recorder(tmp_path / "rec")
        proc = rec.run(
            "approve", "Approve the draft",
            "printf '\\033[32mApproved\\033[0m\\nWarning: x\\n'; echo 'client_secret:  topsecret'; exit 4",
            cwd=tmp_path, capture=True,
        )
        assert proc.returncode == 4
        assert proc.stdout == proc.stderr
        assert "Approved\nWarning: x\n" in proc.stdout, "colour codes stripped, CRLF folded"
        manifest = json.loads((tmp_path / "rec" / "manifest.json").read_text())
        (step,) = manifest["steps"]
        assert (step["step"], step["name"], step["exit_code"], step["cast"]) == (1, "approve", 4, "01-approve.cast")
        cast = (tmp_path / "rec" / "01-approve.cast").read_text()
        assert "topsecret" not in cast and "Approved" in cast
        assert sorted(p.name for p in (tmp_path / "rec").iterdir()) == ["01-approve.cast", "manifest.json"], (
            "the unredacted script logs must not be left behind"
        )

    def test_the_workflow_records_and_keeps_the_footage(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        assert '--record "$RUNNER_TEMP/howto-recording"' in text
        assert "name: howto-recording" in text

    def test_recording_refuses_a_script_without_the_options_it_needs(self, monkeypatch, tmp_path):
        """util-linux 2.34 (Ubuntu 20.04) rejects --logging-format and exits 1 before
        running anything, which would read as every step failing."""
        monkeypatch.setattr(DRIVER, "script_supports_recording", lambda: False)
        with pytest.raises(RuntimeError, match="util-linux `script` 2.35"):
            DRIVER.Recorder(tmp_path / "rec")
        DRIVER.Recorder(None)  # unrecorded runs never need it


class TestFilmedSession:
    """A filmed run reads like a person at a terminal: the README's own lines as
    prompts, its comments dimmed, and the driver's machinery off screen."""

    def _script(self, show):
        qs = DRIVER.extract_quick_start(README.read_text(encoding="utf-8"))
        return qs, DRIVER.build_script(
            qs, tree=Path("/tmp/tree"), tiny_model="t:1", real_pulls=set(),
            handoff=Path("/tmp/h/handoff.env"), show_commands=show,
        )

    def test_each_readme_line_is_shown_as_written(self):
        import subprocess

        qs, script = self._script(True)
        shown = "\n".join(ln for ln in script.splitlines() if ln.startswith("printf"))
        screen = re.sub(r"\x1b\[[0-9;]*m", "", subprocess.run(
            ["bash", "-c", shown], capture_output=True, text=True, check=True,
        ).stdout)
        for line in nonempty([ln.strip() for ln in qs.lines if ln.strip()], "quick start lines"):
            expected = line if line.startswith("#") else f"$ {line}"
            assert expected in screen, f"not shown: {expected}"

    def test_the_drivers_machinery_goes_to_the_notes_file(self):
        _, script = self._script(True)
        machinery = [ln for ln in script.splitlines() if "[quickstart-e2e]" in ln or "settings set" in ln]
        assert machinery and all(ln.endswith(">> /tmp/h/handoff.notes 2>&1") for ln in machinery)

    def test_an_unfilmed_run_is_unchanged(self):
        _, script = self._script(False)
        assert "printf" not in script and "handoff.notes" not in script

    def test_main_films_with_commands_shown(self):
        source = Path(DRIVER.__file__).read_text(encoding="utf-8")
        assert "show_commands=args.record is not None" in source


class TestQaAndPostFootage:
    """The how-to shows what each QA check said and the post a reader gets."""

    def test_a_still_is_kept_and_listed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DRIVER, "script_supports_recording", lambda: True)
        rec = DRIVER.Recorder(tmp_path / "rec")
        path = rec.add_image("post", "The published post", b"\x89PNGdata")
        assert path is not None and path.name == "01-post.png" and path.read_bytes() == b"\x89PNGdata"
        manifest = json.loads((tmp_path / "rec" / "manifest.json").read_text())
        assert manifest["steps"] == [{"step": 1, "name": "post", "title": "The published post", "image": "01-post.png"}]
        assert DRIVER.Recorder(None).add_image("post", "t", b"x") is None

    def test_the_post_is_screenshot_in_the_workers_chromium(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append(argv)
            return DRIVER.subprocess.CompletedProcess(argv, 0, b"\x89PNG...", b"")

        assert DRIVER.screenshot_post("my-post", run=fake_run) == b"\x89PNG..."
        argv = seen[0]
        assert argv[:4] == ["docker", "exec", "poindexter-worker", "python"]
        assert argv[-1] == f"{DRIVER.SITE_URL}/posts/my-post"
        assert "capture_preview_screenshot" in argv[-2]

    @pytest.mark.parametrize("code,out", [(3, b""), (0, b"<html>not a png"), (1, b"\x89PNG")])
    def test_no_screenshot_reads_as_none(self, code, out):
        fake = lambda argv, **kw: DRIVER.subprocess.CompletedProcess(argv, code, out, b"")  # noqa: E731
        assert DRIVER.screenshot_post("s", run=fake) is None

    def test_main_records_the_qa_step_and_the_post(self):
        source = Path(DRIVER.__file__).read_text(encoding="utf-8")
        assert '[pdx, "tasks", "qa", task_id[:8]]' in source
        assert 'recorder.add_image("post"' in source
        assert source.index('"tasks", "qa"') < source.index('"tasks", "approve"')
