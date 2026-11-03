"""Read-only SEO + health audit crawler for a sitemap-driven site.

Fetches every URL in the sitemap, extracts SEO signals, validates structured
data, and flags issues. Prints a summary; optionally writes a full JSON report.

Built for gladlabs.io (default sitemap) but works against any site — pass
``--sitemap``. Read-only: it only issues GET requests, never mutates anything.

Usage::

    python scripts/seo-audit.py                       # crawl gladlabs.io, print summary
    python scripts/seo-audit.py --out report.json     # also dump per-page JSON
    python scripts/seo-audit.py --sitemap https://example.com/sitemap.xml

Flags, per page: missing/over-long title, missing/short/long meta description,
missing canonical, zero/multiple <h1>, missing og:image / og:image:alt /
twitter:card, JSON-LD parse errors, and <img> tags with no alt attribute.

Originally the one-off crawler used in the 2026-06-02 SEO remediation
(Glad-Labs/poindexter#962); kept as a permanent verification tool —
re-run after a content/metadata change to confirm issue counts dropped.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

DEFAULT_SITEMAP = "https://www.gladlabs.io/sitemap.xml"
UA = "Mozilla/5.0 (compatible; GladLabsAudit/1.0; +https://www.gladlabs.io)"
TIMEOUT = 25


def fetch(url: str) -> tuple[int, str, dict, float]:
    import time

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body, dict(r.headers), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, "", {}, time.time() - t0
    except Exception as e:  # noqa: BLE001 — network errors recorded, not raised
        return 0, f"ERROR: {e}", {}, time.time() - t0


class MetaExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = None
        self._in_title = False
        self.metas: list[dict] = []
        self.links: list[dict] = []
        self.imgs: list[dict] = []
        self.h1s: list[str] = []
        self._in_h1 = False
        self._h1_buf = ""
        self.jsonld: list[str] = []
        self._in_jsonld = False
        self._jsonld_buf = ""
        self.a_hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            self.metas.append(d)
        elif tag == "link":
            self.links.append(d)
        elif tag == "img":
            self.imgs.append(d)
        elif tag == "a":
            href = d.get("href")
            if href:
                self.a_hrefs.append(href)
        elif tag == "h1":
            self._in_h1 = True
            self._h1_buf = ""
        elif tag == "script" and d.get("type") == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
            self.h1s.append(self._h1_buf.strip())
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append(self._jsonld_buf)

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data
        if self._in_h1:
            self._h1_buf += data
        if self._in_jsonld:
            self._jsonld_buf += data


def meta_val(metas, *, name=None, prop=None):
    for m in metas:
        if name and m.get("name") == name:
            return m.get("content")
        if prop and m.get("property") == prop:
            return m.get("content")
    return None


def analyze(url: str, status: int, body: str, dur: float) -> dict:
    p = MetaExtractor()
    if body and not body.startswith("ERROR"):
        try:
            p.feed(body)
        except Exception:  # noqa: BLE001 — malformed HTML shouldn't abort the crawl
            pass
    title = (p.title or "").strip()
    desc = meta_val(p.metas, name="description")
    canonical = next((l.get("href") for l in p.links if l.get("rel") == "canonical"), None)
    robots = meta_val(p.metas, name="robots")
    og_image = meta_val(p.metas, prop="og:image")
    og_image_alt = meta_val(p.metas, prop="og:image:alt")
    tw_card = meta_val(p.metas, name="twitter:card")
    tw_image = meta_val(p.metas, name="twitter:image")

    imgs_total = len(p.imgs)
    imgs_no_alt = sum(1 for i in p.imgs if "alt" not in i)
    imgs_empty_alt = sum(1 for i in p.imgs if i.get("alt", None) == "")
    imgs_with_alt = sum(1 for i in p.imgs if i.get("alt"))
    img_alts = [i.get("alt") for i in p.imgs if i.get("alt")]

    jsonld_types: list[str] = []
    jsonld_errors: list[str] = []
    for block in p.jsonld:
        try:
            obj = json.loads(block)
            objs = obj if isinstance(obj, list) else [obj]
            for o in objs:
                if isinstance(o, dict):
                    jsonld_types.append(o.get("@type", "?"))
        except Exception as e:  # noqa: BLE001
            jsonld_errors.append(str(e)[:80])

    issues: list[str] = []
    if status != 200:
        issues.append(f"HTTP {status}")
    if not title:
        issues.append("missing <title>")
    elif len(title) > 65:
        issues.append(f"title too long ({len(title)})")
    if not desc:
        issues.append("missing meta description")
    elif len(desc) > 165:
        issues.append(f"meta desc too long ({len(desc)})")
    elif len(desc) < 50:
        issues.append(f"meta desc too short ({len(desc)})")
    if not canonical:
        issues.append("missing canonical")
    if len(p.h1s) == 0:
        issues.append("no <h1>")
    elif len(p.h1s) > 1:
        issues.append(f"multiple <h1> ({len(p.h1s)})")
    if not og_image:
        issues.append("missing og:image")
    if not og_image_alt:
        issues.append("missing og:image:alt")
    if not tw_card:
        issues.append("missing twitter:card")
    if jsonld_errors:
        issues.append(f"JSON-LD parse error: {jsonld_errors}")
    if imgs_no_alt:
        issues.append(f"{imgs_no_alt} <img> missing alt attr")

    return {
        "url": url,
        "status": status,
        "dur_s": round(dur, 2),
        "bytes": len(body),
        "title": title,
        "title_len": len(title),
        "desc_len": len(desc or ""),
        "canonical": canonical,
        "robots": robots,
        "og_image": bool(og_image),
        "og_image_alt": og_image_alt,
        "tw_card": tw_card,
        "tw_image": bool(tw_image),
        "h1_count": len(p.h1s),
        "imgs_total": imgs_total,
        "imgs_with_alt": imgs_with_alt,
        "imgs_no_alt_attr": imgs_no_alt,
        "imgs_empty_alt": imgs_empty_alt,
        "img_alts_sample": img_alts[:6],
        "jsonld_types": jsonld_types,
        "jsonld_errors": jsonld_errors,
        "n_links": len(p.a_hrefs),
        "issues": issues,
    }


def crawl(sitemap: str, workers: int = 8) -> list[dict]:
    _, body, _, _ = fetch(sitemap)
    urls = re.findall(r"<loc>([^<]+)</loc>", body)
    print(f"Sitemap {sitemap}: {len(urls)} URLs", file=sys.stderr)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, u): u for u in urls}
        for fut in as_completed(futs):
            u = futs[fut]
            st, bd, _hd, du = fut.result()
            results.append(analyze(u, st, bd, du))
    results.sort(key=lambda r: r["url"])
    return results


def print_summary(results: list[dict]) -> None:
    n = len(results)
    print(f"\n===== AUDIT SUMMARY ({n} pages) =====")
    print(f"HTTP non-200: {sum(1 for r in results if r['status'] != 200)}")
    print(f"Missing title: {sum(1 for r in results if not r['title'])}")
    print(f"Missing meta desc: {sum(1 for r in results if r['desc_len'] == 0)}")
    print(f"Meta desc too long (>165): {sum(1 for r in results if r['desc_len'] > 165)}")
    print(f"Missing canonical: {sum(1 for r in results if not r['canonical'])}")
    print(f"No h1: {sum(1 for r in results if r['h1_count'] == 0)}")
    print(f"Multiple h1: {sum(1 for r in results if r['h1_count'] > 1)}")
    print(f"Missing og:image: {sum(1 for r in results if not r['og_image'])}")
    print(f"Missing og:image:alt: {sum(1 for r in results if not r['og_image_alt'])}")
    print(f"Missing twitter:card: {sum(1 for r in results if not r['tw_card'])}")
    print(f"JSON-LD parse errors: {sum(1 for r in results if r['jsonld_errors'])}")
    print(f"Pages w/ <img> missing alt attr: {sum(1 for r in results if r['imgs_no_alt_attr'])}")
    tot_imgs = sum(r["imgs_total"] for r in results)
    print(
        f"Total <img>: {tot_imgs} | with alt: {sum(r['imgs_with_alt'] for r in results)} | "
        f"empty alt: {sum(r['imgs_empty_alt'] for r in results)} | "
        f"no alt attr: {sum(r['imgs_no_alt_attr'] for r in results)}"
    )
    print(f"Slow pages (>3s): {sum(1 for r in results if r['dur_s'] > 3)}")

    types: Counter = Counter()
    for r in results:
        for t in r["jsonld_types"]:
            types[t] += 1
    print(f"JSON-LD types seen: {dict(types)}")

    flagged = [r for r in results if r["issues"]]
    print(f"\nPages with >=1 issue: {len(flagged)}/{n}")
    issue_counter: Counter = Counter()
    for r in flagged:
        for iss in r["issues"]:
            key = re.sub(r"\(\d+\)", "(N)", iss).split(":")[0]
            issue_counter[key] += 1
    print("Issue frequency:")
    for iss, c in issue_counter.most_common():
        print(f"  {c:>4}  {iss}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only SEO/health audit crawler.")
    ap.add_argument("--sitemap", default=DEFAULT_SITEMAP, help="Sitemap URL to crawl.")
    ap.add_argument("--out", help="Optional path to write the full per-page JSON report.")
    ap.add_argument("--workers", type=int, default=8, help="Concurrent fetchers.")
    args = ap.parse_args()

    results = crawl(args.sitemap, workers=args.workers)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"Wrote {len(results)} page records to {args.out}", file=sys.stderr)
    print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
