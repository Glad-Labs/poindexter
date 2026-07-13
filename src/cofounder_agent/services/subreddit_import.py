"""CSV bulk import/export for subreddit_profiles (poindexter community
profiles import-csv / export-csv).

Insert-only by default: a CSV row whose ``subreddit`` already exists is skipped
(never silently overwrites a hand-curated row) unless ``--force``, which updates
it in place. Per-row fail-open: a malformed row becomes an ``error`` result and
the batch continues. No LLM — every column is a literal operator value (unlike
the affiliate importer's keyword derivation). Mirrors
modules/content/affiliate_import.py. See
docs/superpowers/specs/2026-07-13-community-draft-assistant-design.md.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from services.community_drafts import (
    SubredditProfile,
    add_profile,
    list_profiles,
    update_profile,
)

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "subreddit", "enabled", "content_types", "post_type", "self_promo", "flair",
    "min_karma", "min_account_age_days", "rules_summary", "tone_notes", "cadence_cap_days",
]


@dataclass
class ImportRowResult:
    subreddit: str
    status: str            # 'created' | 'updated' | 'skipped_exists' | 'error'
    detail: str = ""


@dataclass
class ImportReport:
    rows: list[ImportRowResult] = field(default_factory=list)

    @property
    def created(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "created"]

    @property
    def updated(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "updated"]

    @property
    def skipped(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "skipped_exists"]

    @property
    def errors(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "error"]


def parse_content_types_cell(raw: str) -> list[str]:
    """';'-delimited cell -> list (a comma-delimited cell would collide with the
    CSV separator, so content_types uses ';')."""
    return [p.strip() for p in (raw or "").split(";") if p.strip()]


def _to_int_or_none(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def _to_bool(raw: str, *, default: bool = True) -> bool:
    raw = (raw or "").strip().lower()
    if raw == "":
        return default
    return raw in ("true", "1", "yes", "y", "t")


def row_to_profile(row: dict) -> SubredditProfile:
    """Pure CSV-row -> SubredditProfile mapping. Missing optional columns take
    the dataclass defaults; blank int cells become None."""
    def g(k: str) -> str:
        return (row.get(k) or "").strip()

    return SubredditProfile(
        subreddit=g("subreddit"),
        enabled=_to_bool(row.get("enabled", ""), default=True),
        content_types=parse_content_types_cell(row.get("content_types", "")),
        post_type=g("post_type") or "text",
        self_promo=g("self_promo") or "strict",
        flair=g("flair") or None,
        min_karma=_to_int_or_none(row.get("min_karma", "")),
        min_account_age_days=_to_int_or_none(row.get("min_account_age_days", "")),
        rules_summary=g("rules_summary"),
        tone_notes=g("tone_notes"),
        cadence_cap_days=_to_int_or_none(row.get("cadence_cap_days", "")),
    )


def profiles_to_csv(profiles: list[SubredditProfile]) -> str:
    """Pure export: profiles -> CSV text in CSV_COLUMNS order. content_types is
    ';'-joined so it survives one cell (symmetric with parse_content_types_cell)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for p in profiles:
        w.writerow([
            p.subreddit, str(p.enabled).lower(), ";".join(p.content_types),
            p.post_type, p.self_promo, p.flair or "",
            "" if p.min_karma is None else p.min_karma,
            "" if p.min_account_age_days is None else p.min_account_age_days,
            p.rules_summary, p.tone_notes,
            "" if p.cadence_cap_days is None else p.cadence_cap_days,
        ])
    return buf.getvalue()


async def import_csv(pool: Any, csv_path: str, *, force: bool = False) -> ImportReport:
    report = ImportReport()
    with open(csv_path, newline="", encoding="utf-8") as f:
        for raw_row in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items()}
            subreddit = row.get("subreddit", "")
            if not subreddit:
                continue
            try:
                profile = row_to_profile(row)
                existing = await pool.fetchval(
                    "SELECT id FROM subreddit_profiles WHERE subreddit = $1", subreddit
                )
                if existing and not force:
                    report.rows.append(ImportRowResult(subreddit, "skipped_exists"))
                    continue
                if existing:
                    await update_profile(pool, profile)
                    report.rows.append(ImportRowResult(subreddit, "updated"))
                else:
                    await add_profile(pool, profile)
                    report.rows.append(ImportRowResult(subreddit, "created"))
            except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
                logger.warning("[subreddit_import] row %r failed: %s", subreddit, exc)
                report.rows.append(ImportRowResult(subreddit, "error", detail=str(exc)))
    return report


async def export_csv(pool: Any) -> str:
    return profiles_to_csv(await list_profiles(pool))


__all__ = [
    "ImportReport", "ImportRowResult", "CSV_COLUMNS", "parse_content_types_cell",
    "row_to_profile", "profiles_to_csv", "import_csv", "export_csv",
]
