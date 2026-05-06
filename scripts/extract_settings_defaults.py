#!/usr/bin/env python3
"""Extract (key, default) pairs from site_config.get*(key, default) calls.

Used once during issue #379 to seed services/settings_defaults.py.
Walks src/cofounder_agent/ and parses every .py file with the AST,
emitting a CSV-ish dump of (key, default_value, kind, file:line) tuples.

Detected patterns (the receiver name is heuristic — anything that *looks*
like a SiteConfig DI seam):

    site_config.get(key, default)
    site_config.get_int(key, default)
    site_config.get_float(key, default)
    site_config.get_bool(key, default)
    site_config.get_list(key, default)

    self._site_config.get*(...)
    self.site_config.get*(...)
    config.get*(...)               # only when receiver name is 'config'

We skip:
    - .get_secret(...)             — secrets must NOT seed
    - .get(...) without a default  — nothing to seed
    - .require(...)                — required, no default
    - service_settings.get(...)    — SettingsService (async, returns None)
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src" / "cofounder_agent"

GETTER_NAMES = {"get", "get_int", "get_float", "get_bool", "get_list"}
RECEIVER_NAMES = {
    "site_config",
    "_site_config",
    # NOTE: 'config' / 'cfg' deliberately excluded — those are job-config
    # dicts in services/jobs/, not SiteConfig instances. Image providers
    # that read a SiteConfig from `config["_site_config"]` will surface as
    # `_site_config` reads after the local rebind.
}

# Files we deliberately exclude from the sweep.
SKIP_DIRS = {"tests", "migrations", "writing_samples"}
# Files that aren't real SiteConfig consumers (they shadow names).
SKIP_FILES = {
    "settings_service.py",  # returns Optional[str], different semantics
    "redis_cache.py",       # cache.get(key) — not config
}


def _attr_chain(node: ast.AST) -> list[str]:
    """Walk node.attr.attr.attr → ['root', 'attr1', 'attr2'] for Attribute/Name."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return list(reversed(parts))


def _value_repr(node: ast.AST) -> tuple[str, str] | None:
    """Render the default-value AST node as (literal_value, source_kind).

    Returns None for non-literal defaults (e.g. variables, function calls).
    """
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None:
            return ("", "none")
        if isinstance(v, bool):
            return ("true" if v else "false", "bool")
        if isinstance(v, (int, float)):
            return (str(v), type(v).__name__)
        if isinstance(v, str):
            return (v, "str")
        return (repr(v), "literal")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _value_repr(node.operand)
        if inner is not None:
            return ("-" + inner[0], inner[1])
    return None


def visit(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out: list[dict] = []

    class V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            self.generic_visit(node)
            func = node.func
            if not isinstance(func, ast.Attribute):
                return
            if func.attr not in GETTER_NAMES:
                return
            chain = _attr_chain(func.value)
            # The receiver should resolve back to one of our DI seams.
            # chain may be ['site_config'] or ['self', '_site_config']
            if not chain:
                return
            recv = chain[-1] if chain[0] == "self" else chain[0]
            if recv not in RECEIVER_NAMES:
                return

            if not node.args:
                return
            key_node = node.args[0]
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                return
            key = key_node.value

            # Default arg: positional [1] or keyword 'default'
            default_node = None
            if len(node.args) >= 2:
                default_node = node.args[1]
            else:
                for kw in node.keywords:
                    if kw.arg == "default":
                        default_node = kw.value
                        break

            if default_node is None:
                return  # no default to seed

            value = _value_repr(default_node)
            if value is None:
                return  # non-literal default, skip

            out.append({
                "key": key,
                "value": value[0],
                "value_type": value[1],
                "getter": func.attr,
                "file": str(path.relative_to(ROOT)),
                "line": node.lineno,
                "receiver": recv,
            })

    V().visit(tree)
    return out


def main() -> int:
    rows: list[dict] = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        # Skip excluded subtrees
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        rows.extend(visit(path))

    # Group by key — pick first default seen, but flag conflicts.
    by_key: dict[str, dict] = {}
    conflicts: dict[str, set[str]] = {}
    for r in rows:
        k = r["key"]
        if k not in by_key:
            by_key[k] = r
        else:
            existing = by_key[k]
            # Normalise booleans: "True"/"true"/"1" all collapse
            def _norm(v: str, t: str) -> str:
                if t == "bool" or v.lower() in ("true", "false"):
                    return v.lower()
                return v.strip()
            a = _norm(existing["value"], existing["value_type"])
            b = _norm(r["value"], r["value_type"])
            if a != b:
                conflicts.setdefault(k, set()).update([f"{existing['file']}:{existing['line']}={existing['value']!r}",
                                                       f"{r['file']}:{r['line']}={r['value']!r}"])

    out_dir = ROOT / "scripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Emit JSON of the unique-key registry (deterministically sorted).
    payload = {
        "rows": sorted(rows, key=lambda x: (x["key"], x["file"], x["line"])),
        "by_key": {k: by_key[k] for k in sorted(by_key)},
        "conflicts": {k: sorted(v) for k, v in sorted(conflicts.items())},
    }
    out_path = out_dir / "settings_defaults_extract.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"  total occurrences:  {len(rows)}")
    print(f"  unique keys:        {len(by_key)}")
    print(f"  conflicting keys:   {len(conflicts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
