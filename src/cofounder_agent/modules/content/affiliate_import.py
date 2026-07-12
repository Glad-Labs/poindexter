"""CSV bulk-import for affiliate_links (poindexter affiliate import-csv).

Insert-only by default: a CSV row whose derived `code` already exists in the
DB is skipped (never silently overwrites a hand-curated row) unless --force.
`is_active` is set directly from the CSV's own Status column. A local LLM
proposes display_text + keywords from title+description; failures fall
open per-row (the row still gets created, using the product name itself as
a fallback keyword) so one bad row never aborts the whole import. See
docs/superpowers/specs/2026-07-12-affiliate-multi-keyword-platform-design.md.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from services.llm_text import ollama_chat_text, resolve_structured_model

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_PROMPT_KEY = "task.affiliate_derive_keywords"
_PROMPT_FALLBACK = """\
You are naming and tagging a product for an affiliate-link catalog.

Given the product title and description below, respond with ONLY a JSON
object: {{"display_text": "<short human name, 2-6 words>", "keywords":
["<alias 1>", "<alias 2>", ...]}}

- display_text: a short, natural name a reader would recognize (not the
  full marketing title).
- keywords: 3 to 6 short phrases (1-4 words each) that might plausibly
  appear in prose referring to this product - brand names, model numbers,
  common nicknames. Avoid generic single words that could describe many
  products.

Title: {title}
Description: {description}
"""


def slugify_code(product_name: str, *, max_words: int = 6, max_len: int = 60) -> str:
    """Deterministic, non-LLM code derivation -- stable across re-imports."""
    words = product_name.strip().split()[:max_words]
    slug = _SLUG_RE.sub("-", " ".join(words).lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _map_category(csv_category: str) -> str:
    return "service" if csv_category.strip().lower() == "service" else "product"


def _map_is_active(csv_status: str) -> bool:
    return csv_status.strip().lower() == "active"


@dataclass
class ImportRowResult:
    code: str
    product_name: str
    status: str  # "created" | "skipped_exists" | "error"
    display_text: str = ""
    keywords: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class ImportReport:
    rows: list[ImportRowResult] = field(default_factory=list)

    @property
    def created(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "created"]

    @property
    def skipped(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "skipped_exists"]

    @property
    def errors(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "error"]


def _resolve_prompt(*, title: str, description: str) -> str:
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _PROMPT_KEY, title=title, description=description,
        )
    except Exception as exc:  # noqa: BLE001 — registry unreachable (bootstrap/test)
        logger.error("[affiliate_import] prompt lookup failed (%s) — inline fallback", exc)
        return _PROMPT_FALLBACK.format(title=title, description=description)


def _parse_llm_json(raw: str) -> dict:
    if not raw:
        return {}
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


async def _derive_display_and_keywords(
    *, title: str, description: str, site_config: Any, pool: Any,
) -> tuple[str, list[str]]:
    """LLM-assisted; fails open to (title[:60], []) on any error so one bad
    row never aborts the batch."""
    try:
        pin = (site_config.get("affiliate_import_llm_model", "") or "").strip() or None
        model = resolve_structured_model(pin, site_config=site_config)
        prompt = _resolve_prompt(title=title, description=description)
        timeout = site_config.get_int("affiliate_import_llm_timeout_seconds", 60)
        raw = await ollama_chat_text(
            prompt, model=model, site_config=site_config, pool=pool,
            tier="budget", timeout_setting="affiliate_import_llm_timeout_seconds",
            timeout_default=float(timeout), phase="affiliate_import_derive_keywords",
            think=False,
        )
        parsed = _parse_llm_json(raw)
        display_text = str(parsed.get("display_text") or "").strip() or title[:60]
        keywords = [str(k).strip() for k in (parsed.get("keywords") or []) if str(k).strip()]
        return display_text, keywords
    except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
        logger.warning("[affiliate_import] LLM derivation failed for %r: %s", title[:60], exc)
        return title[:60], []


async def import_csv(
    pool: Any, csv_path: str, *, site_config: Any, force: bool = False,
) -> ImportReport:
    report = ImportReport()
    from modules.content.affiliate_links import add_link, set_active

    with open(csv_path, newline="", encoding="utf-8") as f:
        for raw_row in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items()}
            product_name = row.get("Product Name", "")
            if not product_name:
                continue
            code = slugify_code(product_name)

            existing = await pool.fetchval("SELECT id FROM affiliate_links WHERE code = $1", code)
            if existing and not force:
                report.rows.append(
                    ImportRowResult(code=code, product_name=product_name, status="skipped_exists")
                )
                continue

            description = row.get("Description", "")
            display_text, keywords = await _derive_display_and_keywords(
                title=product_name, description=description,
                site_config=site_config, pool=pool,
            )
            try:
                await add_link(
                    pool, code=code, keywords=keywords or [product_name[:60]],
                    url=row.get("Affiliate Link", ""), display_text=display_text,
                    description=description,
                    category=_map_category(row.get("Category", "")),
                    platform=row.get("Platform", ""),
                )
                await set_active(pool, code, _map_is_active(row.get("Status", "")))
                report.rows.append(
                    ImportRowResult(
                        code=code, product_name=product_name, status="created",
                        display_text=display_text, keywords=keywords or [product_name[:60]],
                    )
                )
            except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
                report.rows.append(
                    ImportRowResult(code=code, product_name=product_name, status="error", detail=str(exc))
                )
    return report


__all__ = ["ImportReport", "ImportRowResult", "slugify_code", "import_csv"]
