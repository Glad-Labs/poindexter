"""Pack — a bundled prompts / styles / QA workflow configuration.

Unlike the other five plugin types, a Pack is *data*, not code. It
ships as a pypi package that drops its contents into the Poindexter DB
on install:

- ``writing_styles`` app_setting — JSON array of voice configurations
- ``image_styles`` app_setting — JSON array of image-gen style prompts
- ``qa_gates`` rows — reviewer-rail gate configuration

(Prompt bodies are NOT DB data — they ship as SKILL.md packs in the
repo; the ``prompt_templates`` table this design originally targeted
was retired in poindexter#47 Phase 2.)

Distribution:

- **Free (community):** ``poindexter-pack-community`` on public pypi,
  Apache-2.0.
- **Premium:** delivered to Pro subscribers through the private
  ``poindexter-pro`` repo (GitHub collaborator invite,
  glad-labs-stack#3216 — the earlier Lemon Squeezy license-key
  activation flow is retired).

Installation flow:

1. ``pip install poindexter-pack-community`` (Pro packs arrive via the
   ``poindexter-pro`` repo instead)
2. Package exposes an entry_point under ``poindexter.packs``
3. On worker boot, each registered Pack's ``apply()`` is called with
   an idempotent ``INSERT ... ON CONFLICT`` so re-runs are safe

Register a Pack via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.packs"]
    community = "poindexter_pack_community:CommunityPack"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class PackMetadata:
    """Descriptive info for an installed Pack. Surfaced in operator UIs."""

    name: str
    version: str
    tier: str  # "free" | "premium"
    description: str = ""
    license_required: bool = False
    published_at: str = ""  # ISO-8601


@runtime_checkable
class Pack(Protocol):
    """A bundle of prompt + style + workflow data loaded into the DB on install.

    Attributes:
        name: Unique pack name.
        version: Semver.
        tier: ``"free"`` or ``"premium"``.
    """

    name: str
    version: str
    tier: str

    async def apply(
        self,
        pool: Any,  # asyncpg.Pool
    ) -> None:
        """Load this Pack's contents into the DB.

        Implementations should:

        - Upsert JSON-blob app_settings (``writing_styles``,
          ``image_styles``) and ``qa_gates`` rows using a "newer-wins"
          strategy based on the Pack's ``version``
        - Be idempotent — safe to call on every worker boot

        (Prompt bodies are not applied here — they ship as SKILL.md
        packs in the repo, not DB rows.)
        """
        ...

    def metadata(self) -> PackMetadata:
        """Return descriptive info for operator UIs."""
        ...
