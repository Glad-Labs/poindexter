"""Audit all published posts for quality issues."""
import json
import re
import sys

with open(sys.argv[1]) as f:
    data = json.load(f)

posts = data.get("posts", [])

for p in posts:
    title = p.get("title", "")[:65]
    content = p.get("content", "") or ""
    img = p.get("featured_image", "") or p.get("featured_image_url", "") or ""
    words = len(content.split())
    issues = []

    we_count = len(re.findall(r"\b(?:we|our|us)\b", content.lower()))
    if we_count > 5:
        issues.append(f"VOICE:{we_count}x we/our")

    if "[IMAGE" in content:
        issues.append("PLACEHOLDER")

    bare = re.findall(r"(?<!\()(https?://\S+)(?!\))", content)
    if bare:
        issues.append(f"BARE_URL:{len(bare)}")

    if img and "pexels" in img.lower():
        issues.append("STOCK_IMG")
    elif not img:
        issues.append("NO_IMG")

    if title.startswith("The "):
        issues.append("THE_TITLE")

    marker = "!!!" if len(issues) >= 3 else "! " if issues else "OK"
    issue_str = ", ".join(issues) if issues else "clean"
    print(f"[{marker}] {words:4d}w | {issue_str:45s} | {title}")

print(f"\nTotal: {len(posts)} posts")
print(f"  With 'The' titles: {sum(1 for p in posts if p.get('title', '').startswith('The '))}")
print(f"  Stock images: {sum(1 for p in posts if 'pexels' in (p.get('featured_image', '') or '').lower())}")
