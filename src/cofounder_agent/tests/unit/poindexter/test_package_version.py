"""``poindexter.package_version()`` is what Sentry ``release`` tags carry.

It lives in ``poindexter/__init__.py`` on purpose: that file ships in every
image (the brain image copies only ``__init__.py`` + ``brain/``), and it must
never raise -- a version string is telemetry seasoning, not a boot dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

import tomllib

import poindexter


def test_package_version_matches_the_backend_manifest():
    manifest = Path(poindexter.__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["version"]
    assert poindexter.package_version() == expected
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", expected)


def test_package_version_never_raises(monkeypatch):
    # No manifest reachable and no installed distribution -> a sentinel, not an error.
    import importlib.metadata as md

    monkeypatch.setattr(poindexter, "__file__", "/nonexistent/poindexter/__init__.py")
    monkeypatch.setattr(md, "version", lambda _n: (_ for _ in ()).throw(md.PackageNotFoundError("poindexter")))
    assert poindexter.package_version() == "0.0.0+unknown"
