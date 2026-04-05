import json, re

with open("C:\Users\mattm\glad-labs-website\scripts\posts_audit.json") as f:
    data = json.load(f)

posts = data.get("posts", [])
for p in posts:
    content = p.get("content", "") or ""
    we_count = len(re.findall(r"\b(?:we|our|us)\b", content.lower()))
    if we_count > 5:
        print(f"TAKEDOWN | id={p['id']} | we/our={we_count} | {p['title'][:60]}")
