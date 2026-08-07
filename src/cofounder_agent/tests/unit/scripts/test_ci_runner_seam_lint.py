"""Guard for the CI_RUNNER seam lint.

The seam's capability model is invisible from a job definition: the
self-hosted runners are containers with no ``/var/run/docker.sock`` (they
execute raw PR code, so the daemon running the production stack is
deliberately out of reach). A job that needs Docker therefore dies at
"Initialize containers" before any step runs.

That has shipped twice — ``docker-build``'s ``build-worker`` (#2920, red on
every main push) and ``benchmarks``' ``services: postgres`` (failed nightly
for weeks behind a stale "runner is a host process with Docker access"
comment). These tests pin the detection so it stays mechanical.

The regex cases matter as much as the structural ones: the first draft of
the docker-CLI heuristic flagged ``emit docker true`` — a shell helper in
security.yml's path classifier — which would have blocked a legitimate job.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_LINT = Path(__file__).resolve().parents[5] / "scripts" / "ci" / "ci_runner_seam_lint.py"

_SEAM = "${{ vars.CI_RUNNER && fromJSON(vars.CI_RUNNER) || 'ubuntu-latest' }}"


def _load():
    spec = importlib.util.spec_from_file_location(_LINT.stem, _LINT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestDockerCommandRegex:
    """Command-position + real-subcommand, not a bare substring match."""

    @pytest.mark.parametrize(
        "text",
        [
            "docker build -t x .",
            "  docker compose up -d",  # indented — the common `run: |` shape
            "run: |\n  docker buildx build .",
            "sudo docker run x",
            "a && docker push x",
        ],
    )
    def test_matches_real_invocations(self, text: str):
        assert _load()._DOCKER_CMD.search(text), f"should flag: {text!r}"

    @pytest.mark.parametrize(
        ("text", "why"),
        [
            ("emit docker true", "shell helper — the real false positive that bit"),
            ("grep -qiE 'dockerfile|docker-compose'", "grep pattern, not a call"),
            ("echo docker build", "echoed, not command position"),
            ("my_docker_helper build", "substring inside an identifier"),
            ("# docker build (comment)", "commented out"),
        ],
    )
    def test_ignores_non_invocations(self, text: str, why: str):
        assert not _load()._DOCKER_CMD.search(text), f"false positive ({why}): {text!r}"


class TestDockerReasons:
    def test_flags_services(self):
        job = {"runs-on": _SEAM, "services": {"postgres": {"image": "pgvector/pgvector:pg16"}}}
        reasons = _load()._docker_reasons(job)
        assert any("services" in r for r in reasons)
        assert any("postgres" in r for r in reasons)

    def test_flags_container(self):
        assert _load()._docker_reasons({"container": "python:3.13"})

    def test_flags_known_docker_actions(self):
        job = {"steps": [{"uses": "aquasecurity/trivy-action@ed142fd"}]}
        assert _load()._docker_reasons(job)

    def test_allows_the_proven_native_actions(self):
        """Exactly what the already-working seam jobs use."""
        job = {
            "steps": [
                {"uses": "actions/checkout@3d3c42e"},
                {"uses": "actions/setup-python@5fda3b9"},
                {"uses": "actions/setup-node@8207627"},
                {"uses": "actions/upload-artifact@043fb46"},
                {"run": "poetry run pytest -q"},
            ]
        }
        assert _load()._docker_reasons(job) == []


class TestSeamDetection:
    def test_ci_runner_is_the_seam(self):
        assert _load()._uses_seam(_SEAM)

    def test_docker_seam_is_a_separate_axis(self):
        """CI_RUNNER_DOCKER is for a daemon-capable runner — not this guard's
        business, or it would flag docker-build's dormant seam forever."""
        docker_seam = "${{ vars.CI_RUNNER_DOCKER && fromJSON(vars.CI_RUNNER_DOCKER) || 'ubuntu-latest' }}"
        assert not _load()._uses_seam(docker_seam)

    def test_plain_hosted_is_not_the_seam(self):
        assert not _load()._uses_seam("ubuntu-latest")


class TestBarePythonReasons:
    """`python` exists on hosted images; the runner image has only `python3`.

    Regression guard for the real failure this PR hit: `security.yml`'s
    `action-pins` moved onto the seam and died with
    `python: command not found` (exit 127) on `glads-pc-JVTD56YcptsSm`.
    """

    def test_flags_bare_python_without_setup_python(self):
        job = {
            "steps": [
                {"uses": "actions/checkout@abc"},
                {"run": "python scripts/ci/check-action-pins.py"},
            ]
        }
        reasons = _load()._bare_python_reasons(job)
        assert reasons and "python3" in reasons[0]

    def test_python3_is_fine(self):
        job = {"steps": [{"run": "python3 scripts/ci/x.py"}]}
        assert _load()._bare_python_reasons(job) == []

    def test_setup_python_provides_the_shim(self):
        """A job that installs Python may use the bare name either way."""
        job = {
            "steps": [
                {"uses": "actions/setup-python@abc"},
                {"run": "python -m pytest"},
            ]
        }
        assert _load()._bare_python_reasons(job) == []

    @pytest.mark.parametrize(
        "line",
        [
            "echo python foo",  # not command position
            "poetry run python -m pytest",  # not command position
            "python3.13 -m x",  # versioned interpreter
            "my_python thing",  # substring
        ],
    )
    def test_ignores_non_invocations(self, line: str):
        job = {"steps": [{"run": line}]}
        assert _load()._bare_python_reasons(job) == [], line


class TestScannerMatchesYaml:
    """The stdlib scanner must agree with a real YAML parse.

    The lint ships parser-free because it runs under the runner's system
    interpreter, which has no PyYAML — but a hand-rolled scanner that
    silently misreads a file is worse than no lint. Tests run in the poetry
    env (PyYAML present), so they can hold the scanner to the real parse.

    This caught a real miss: the scanner assumed 2-space job indentation and
    skipped release-mirror-to-public.yml and release-please.yml, which use 4.
    """

    def test_agrees_with_yaml_on_every_workflow(self):
        mod = _load()
        wf_dir = Path(__file__).resolve().parents[5] / ".github" / "workflows"
        checked = 0
        for path in sorted(wf_dir.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            truth = yaml.safe_load(text) or {}
            tjobs = {
                k: v for k, v in (truth.get("jobs") or {}).items() if isinstance(v, dict)
            }
            sjobs = mod.scan_jobs(text)

            assert set(tjobs) == set(sjobs), f"{path.name}: job set differs"
            for name, tj in tjobs.items():
                sj = sjobs[name]
                where = f"{path.name}::{name}"
                assert mod._uses_seam(str(tj.get("runs-on", ""))) == mod._uses_seam(
                    str(sj.get("runs-on", ""))
                ), f"{where}: seam detection differs"
                assert set(tj.get("services") or {}) == set(
                    sj.get("services") or {}
                ), f"{where}: services differ"
                assert bool(tj.get("container")) == bool(
                    sj.get("container")
                ), f"{where}: container differs"
                assert bool(mod._docker_reasons(tj)) == bool(
                    mod._docker_reasons(sj)
                ), f"{where}: verdict differs"
                checked += 1
        assert checked >= 35, f"expected the full workflow set, checked {checked}"

    def test_handles_four_space_job_indentation(self):
        """The exact shape that was silently skipped."""
        text = (
            "name: x\n"
            "on: push\n"
            "jobs:\n"
            "    deep-job:\n"
            "        runs-on: ubuntu-latest\n"
            "        steps:\n"
            "          - uses: actions/checkout@abc\n"
        )
        jobs = _load().scan_jobs(text)
        assert set(jobs) == {"deep-job"}
        assert jobs["deep-job"]["runs-on"] == "ubuntu-latest"


class TestRepoIsClean:
    """The live tree must pass — this is the ratchet."""

    def test_lint_passes_on_the_real_workflows(self):
        assert _load().main() == 0

    def test_every_seam_job_parses_and_is_docker_free(self):
        mod = _load()
        wf_dir = Path(__file__).resolve().parents[5] / ".github" / "workflows"
        seam_jobs = 0
        for path in sorted(wf_dir.glob("*.yml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for name, job in (doc.get("jobs") or {}).items():
                if isinstance(job, dict) and mod._uses_seam(job.get("runs-on", "")):
                    seam_jobs += 1
                    assert mod._docker_reasons(job) == [], f"{path.name}::{name}"
        # Guard against the seam silently collapsing back to a handful of jobs.
        assert seam_jobs >= 15, f"expected the widened seam, found {seam_jobs} jobs"

    def test_required_checks_can_fail_over(self):
        """The gating checks are the ones that must not hang in an outage.

        migrations-smoke is the documented exception: it needs a Postgres
        service container, which the runners cannot start.
        """
        mod = _load()
        wf_dir = Path(__file__).resolve().parents[5] / ".github" / "workflows"
        on_seam = set()
        for path in sorted(wf_dir.glob("*.yml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for name, job in (doc.get("jobs") or {}).items():
                if isinstance(job, dict) and mod._uses_seam(job.get("runs-on", "")):
                    on_seam.add(name)

        expected = {
            "test-backend",
            "backend-lint",
            "syntax-check",
            "mcp-server-tests",
            "public-mirror-safety",
            "gitleaks",
        }
        assert expected <= on_seam, f"required checks off the seam: {expected - on_seam}"
