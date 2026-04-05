"""Generate static feed.xml and sitemap.xml from local Postgres.

Run during build before `next build` to produce static files
that don't need a runtime API.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

SITE_URL = "https://www.gladlabs.io"
PUBLIC_DIR = Path(__file__).parent.parent / "web" / "public-site" / "public"
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain",
)


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


async def main():
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    # Fetch published posts
    async with pool.acquire() as conn:
        posts = await conn.fetch(
            """
            SELECT title, slug, excerpt, seo_description, featured_image_url,
                   published_at, updated_at
            FROM posts
            WHERE status = 'published'
            ORDER BY published_at DESC NULLS LAST
            LIMIT 50
            """
        )
        categories = await conn.fetch("SELECT slug FROM categories ORDER BY name")

    await pool.close()

    print(f"Fetched {len(posts)} published posts, {len(categories)} categories")

    # --- Generate feed.xml ---
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    latest = posts[0]["published_at"].strftime("%a, %d %b %Y %H:%M:%S +0000") if posts else now

    items = []
    for p in posts[:20]:
        pub_date = p["published_at"].strftime("%a, %d %b %Y %H:%M:%S +0000") if p["published_at"] else now
        desc = p["seo_description"] or p["excerpt"] or p["title"]
        link = f"{SITE_URL}/posts/{p['slug']}"
        enclosure = ""
        if p["featured_image_url"]:
            enclosure = f'\n      <enclosure url="{escape_xml(p["featured_image_url"])}" type="image/jpeg" />'
        items.append(f"""    <item>
      <title><![CDATA[{p['title']}]]></title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <description><![CDATA[{desc}]]></description>
      <pubDate>{pub_date}</pubDate>{enclosure}
    </item>""")

    feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Glad Labs</title>
    <link>{SITE_URL}</link>
    <description>Technology, AI, and digital innovation — in-depth articles for developers and founders.</description>
    <language>en-us</language>
    <lastBuildDate>{latest}</lastBuildDate>
    <atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml" />
    <image>
      <url>{SITE_URL}/og-image.jpg</url>
      <title>Glad Labs</title>
      <link>{SITE_URL}</link>
    </image>
{chr(10).join(items)}
  </channel>
</rss>"""

    feed_path = PUBLIC_DIR / "feed.xml"
    feed_path.write_text(feed_xml, encoding="utf-8")
    print(f"Written {feed_path} ({len(items)} items)")

    # --- Generate sitemap.xml ---
    urls = [
        f"  <url><loc>{SITE_URL}</loc><changefreq>daily</changefreq><priority>1.0</priority></url>",
        f"  <url><loc>{SITE_URL}/archive</loc><changefreq>daily</changefreq><priority>0.8</priority></url>",
    ]
    for cat in categories:
        urls.append(f"  <url><loc>{SITE_URL}/category/{cat['slug']}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>")
    for p in posts:
        updated = p["updated_at"].strftime("%Y-%m-%d") if p["updated_at"] else ""
        lastmod = f"<lastmod>{updated}</lastmod>" if updated else ""
        urls.append(f"  <url><loc>{SITE_URL}/posts/{p['slug']}</loc>{lastmod}<changefreq>monthly</changefreq><priority>0.6</priority></url>")

    sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    sitemap_path = PUBLIC_DIR / "sitemap.xml"
    sitemap_path.write_text(sitemap_xml, encoding="utf-8")
    print(f"Written {sitemap_path} ({len(urls)} URLs)")


if __name__ == "__main__":
    asyncio.run(main())
