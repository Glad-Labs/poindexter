"""GlitchTip audit script (one-shot).

Logs into GlitchTip with admin credentials, pulls every unresolved issue
from the default org, and emits a categorized JSON + markdown report.

Output:
  - scripts/glitchtip_audit_raw.json (every issue payload, full)
  - scripts/glitchtip_audit_report.md (human-readable summary)

Usage:
  python scripts/glitchtip_audit.py
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

GLITCHTIP_BASE = os.environ.get("GLITCHTIP_BASE_URL", "http://localhost:8080")
EMAIL = os.environ.get("GLITCHTIP_ADMIN_EMAIL", "matt@gladlabs.io")
PASSWORD = os.environ.get("GLITCHTIP_ADMIN_PASSWORD", "p2sehys__-WocryASHbk1Q")
# Optional API token (preferred — skips the session login dance entirely).
API_TOKEN = os.environ.get("GLITCHTIP_API_TOKEN", "").strip()

OUT_DIR = Path(__file__).resolve().parent
RAW_PATH = OUT_DIR / "glitchtip_audit_raw.json"
REPORT_PATH = OUT_DIR / "glitchtip_audit_report.md"


def login(client: httpx.Client) -> None:
    """Establish a session via the django-allauth headless browser API.

    GlitchTip exposes the allauth headless API at
    ``/_allauth/browser/v1/auth/login`` which expects
    ``{"email", "password"}`` JSON. A successful login sets a sessionid
    cookie on the client; subsequent ``/api/0/`` calls work as that user.

    See https://docs.allauth.org/en/latest/headless/openapi-specification/
    """
    # Prime cookies.
    r = client.get(f"{GLITCHTIP_BASE}/")
    r.raise_for_status()
    csrf = client.cookies.get("csrftoken", "")
    headers = {
        "Referer": f"{GLITCHTIP_BASE}/",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if csrf:
        headers["X-CSRFToken"] = csrf
    payload = {"email": EMAIL, "password": PASSWORD}
    r = client.post(
        f"{GLITCHTIP_BASE}/_allauth/browser/v1/auth/login",
        headers=headers,
        json=payload,
    )
    if r.status_code >= 400:
        raise RuntimeError(
            f"Login failed: {r.status_code} {r.text[:400]}"
        )
    body = r.json()
    print(
        f"Login OK: {r.status_code} status={body.get('status')} "
        f"meta={body.get('meta')}  cookies={list(client.cookies.keys())}"
    )


def list_orgs(client: httpx.Client) -> list[dict]:
    r = client.get(f"{GLITCHTIP_BASE}/api/0/organizations/")
    r.raise_for_status()
    return r.json()


def fetch_all_issues(client: httpx.Client, org_slug: str) -> list[dict]:
    issues: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        params = {"query": "is:unresolved", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = client.get(
            f"{GLITCHTIP_BASE}/api/0/organizations/{org_slug}/issues/",
            params=params,
        )
        if r.status_code == 404:
            # Some GlitchTip versions use /api/0/issues/?organization=
            r = client.get(
                f"{GLITCHTIP_BASE}/api/0/issues/",
                params={**params, "organization": org_slug},
            )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        issues.extend(batch)
        page += 1
        # Parse Link header for next cursor.
        link = r.headers.get("Link", "")
        next_cursor = None
        for part in link.split(","):
            if 'rel="next"' in part and 'results="true"' in part:
                m = re.search(r"cursor=([^&>]+)", part)
                if m:
                    next_cursor = m.group(1)
                break
        if not next_cursor:
            break
        cursor = next_cursor
        if page > 100:  # hard safety
            print("Hit page-cap 100 — bailing", file=sys.stderr)
            break
    return issues


def categorize(issues: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    one_h = now - timedelta(hours=1)
    one_d = now - timedelta(hours=24)

    by_type: collections.Counter = collections.Counter()
    by_module: collections.Counter = collections.Counter()
    by_recency = {"last_1h": 0, "last_24h": 0, "older": 0}
    by_count_band = {"high (>1000)": 0, "medium (100-1000)": 0, "low (5-100)": 0, "noise (<5)": 0}
    titles_to_count: list[tuple[str, int, str, str, str]] = []
    high_count_issues: list[dict] = []
    recent_issues: list[dict] = []

    for issue in issues:
        title = issue.get("title") or "(no title)"
        culprit = issue.get("culprit") or ""
        count = int(issue.get("count") or 0)
        last_seen_raw = issue.get("lastSeen") or ""
        first_seen_raw = issue.get("firstSeen") or ""
        level = issue.get("level") or "error"
        meta = issue.get("metadata") or {}
        exc_type = meta.get("type") or _infer_type(title)

        by_type[exc_type] += 1

        module = _module_from_culprit(culprit) or _module_from_title(title) or "(unknown)"
        by_module[module] += 1

        try:
            last_seen = datetime.fromisoformat(last_seen_raw.replace("Z", "+00:00"))
        except Exception:
            last_seen = None

        if last_seen and last_seen >= one_h:
            by_recency["last_1h"] += 1
        elif last_seen and last_seen >= one_d:
            by_recency["last_24h"] += 1
        else:
            by_recency["older"] += 1

        if count > 1000:
            by_count_band["high (>1000)"] += 1
        elif count >= 100:
            by_count_band["medium (100-1000)"] += 1
        elif count >= 5:
            by_count_band["low (5-100)"] += 1
        else:
            by_count_band["noise (<5)"] += 1

        titles_to_count.append((title, count, exc_type, last_seen_raw, culprit))

        if count >= 100:
            high_count_issues.append({
                "id": issue.get("id"),
                "title": title,
                "type": exc_type,
                "culprit": culprit,
                "count": count,
                "lastSeen": last_seen_raw,
                "permalink": issue.get("permalink"),
            })

        if last_seen and last_seen >= one_h:
            recent_issues.append({
                "id": issue.get("id"),
                "title": title,
                "type": exc_type,
                "count": count,
                "lastSeen": last_seen_raw,
            })

    titles_to_count.sort(key=lambda t: t[1], reverse=True)

    return {
        "total": len(issues),
        "by_type_top": by_type.most_common(15),
        "by_module_top": by_module.most_common(15),
        "by_recency": by_recency,
        "by_count_band": by_count_band,
        "top_by_count": titles_to_count[:25],
        "high_count_issues": high_count_issues,
        "recent_issues": recent_issues[:25],
    }


def _module_from_culprit(culprit: str) -> str | None:
    if not culprit:
        return None
    # Culprit usually looks like "module.path in func" or just "module.path"
    head = culprit.split(" in ")[0].strip()
    if not head:
        return None
    # Strip trailing .func to get the module
    parts = head.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else head


def _module_from_title(title: str) -> str | None:
    # Some titles are "ExceptionType: message at module.path:line"
    m = re.search(r"\bat\s+([\w\.]+):\d+", title)
    if m:
        return m.group(1).rsplit(".", 1)[0]
    return None


def _infer_type(title: str) -> str:
    # "ExceptionType: ..."
    m = re.match(r"^([A-Z][\w\.]*Error|[A-Z][\w\.]*Exception|[A-Z][\w\.]*Warning|[A-Z][\w\.]*)\s*:", title)
    if m:
        return m.group(1)
    return "(unknown)"


def render_report(summary: dict, org_slug: str) -> str:
    lines: list[str] = []
    lines.append(f"# GlitchTip audit — {org_slug}")
    lines.append("")
    lines.append(f"Total open (unresolved) issues: **{summary['total']}**")
    lines.append("")

    lines.append("## Recency")
    for k, v in summary["by_recency"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## Count bands (signal vs noise)")
    for k, v in summary["by_count_band"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## Top exception types")
    for t, n in summary["by_type_top"][:10]:
        lines.append(f"- `{t}` — {n} distinct issue(s)")
    lines.append("")

    lines.append("## Noisiest modules")
    for m, n in summary["by_module_top"][:10]:
        lines.append(f"- `{m}` — {n} distinct issue(s)")
    lines.append("")

    lines.append("## Top 10 issues by occurrence count")
    lines.append("")
    lines.append("| Count | Type | Title | Last seen |")
    lines.append("|---:|---|---|---|")
    for title, count, exc_type, last_seen, culprit in summary["top_by_count"][:10]:
        safe_title = title.replace("|", "\\|")[:120]
        lines.append(f"| {count} | `{exc_type}` | {safe_title} | {last_seen} |")
    lines.append("")

    lines.append("## Recent (last hour)")
    if summary["recent_issues"]:
        for r in summary["recent_issues"][:10]:
            lines.append(f"- ({r['count']}x) `{r['type']}` — {r['title'][:120]}")
    else:
        lines.append("- (none)")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    headers: dict[str, str] = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        if not API_TOKEN:
            login(client)
        orgs = list_orgs(client)
        if not orgs:
            print("No organizations visible to this user", file=sys.stderr)
            return 1
        # GlitchTip /api/0/organizations/ returns list with at least slug.
        org_slug = orgs[0].get("slug") or orgs[0].get("name", "default")
        print(f"Using org: {org_slug} ({len(orgs)} org(s) total)")

        issues = fetch_all_issues(client, org_slug)
        print(f"Pulled {len(issues)} unresolved issue(s)")

        RAW_PATH.write_text(json.dumps(issues, indent=2), encoding="utf-8")
        print(f"Wrote raw payload: {RAW_PATH}")

        summary = categorize(issues)
        report = render_report(summary, org_slug)
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(f"Wrote report: {REPORT_PATH}")
        print()
        print(report)
        return 0


if __name__ == "__main__":
    sys.exit(main())
