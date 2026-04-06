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
R2_URL = "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev"
PUBLIC_DIR = Path(__file__).parent.parent / "web" / "public-site" / "public"
PODCAST_DIR = Path.home() / ".gladlabs" / "podcast"
VIDEO_DIR = Path.home() / ".gladlabs" / "video"
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

    # --- Generate podcast feed.xml ---
    # Find podcast episodes on disk that have matching published posts
    podcast_ids = set()
    if PODCAST_DIR.exists():
        for mp3 in PODCAST_DIR.glob("*.mp3"):
            name = mp3.stem
            if not name.startswith("test_") and not name.startswith("jingle_"):
                podcast_ids.add(name)

    if podcast_ids:
        async with (await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)).acquire() as conn:
            podcast_posts = await conn.fetch(
                """
                SELECT id::text, title, slug, excerpt, published_at
                FROM posts
                WHERE id::text = ANY($1) AND status = 'published'
                ORDER BY published_at DESC
                """,
                list(podcast_ids),
            )

        pod_items = []
        for p in podcast_posts:
            pid = p["id"]
            mp3_path = PODCAST_DIR / f"{pid}.mp3"
            if not mp3_path.exists():
                continue
            size = mp3_path.stat().st_size
            pub_date = p["published_at"].strftime("%a, %d %b %Y %H:%M:%S +0000") if p["published_at"] else now
            desc = escape_xml(p["excerpt"] or p["title"])
            pod_items.append(f"""    <item>
      <title><![CDATA[{p['title']}]]></title>
      <link>{SITE_URL}/posts/{p['slug']}</link>
      <description>{desc}</description>
      <guid>gladlabs-podcast-{pid}</guid>
      <pubDate>{pub_date}</pubDate>
      <enclosure url="{R2_URL}/podcast/{pid}.mp3" length="{size}" type="audio/mpeg" />
      <itunes:explicit>no</itunes:explicit>
    </item>""")

        podcast_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Glad Labs Podcast</title>
    <link>{SITE_URL}</link>
    <language>en-us</language>
    <description>AI development insights, local LLM guides, and behind-the-scenes of building an AI-operated content business.</description>
    <itunes:author>Glad Labs</itunes:author>
    <itunes:summary>AI development insights and guides from an AI-operated content business.</itunes:summary>
    <itunes:owner>
      <itunes:name>Matt Gladding</itunes:name>
      <itunes:email>mattg@gladlabs.io</itunes:email>
    </itunes:owner>
    <itunes:category text="Technology" />
    <itunes:explicit>no</itunes:explicit>
    <atom:link href="{SITE_URL}/podcast-feed.xml" rel="self" type="application/rss+xml" />
{chr(10).join(pod_items)}
  </channel>
</rss>"""

        pod_path = PUBLIC_DIR / "podcast-feed.xml"
        pod_path.write_text(podcast_xml, encoding="utf-8")
        print(f"Written {pod_path} ({len(pod_items)} episodes)")
    else:
        print("No podcast episodes found on disk")

    # --- Generate video feed.xml ---
    video_ids = set()
    if VIDEO_DIR.exists():
        for mp4 in VIDEO_DIR.glob("*.mp4"):
            video_ids.add(mp4.stem)

    if video_ids:
        async with (await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)).acquire() as conn:
            video_posts = await conn.fetch(
                """
                SELECT id::text, title, slug, excerpt, published_at
                FROM posts
                WHERE id::text = ANY($1) AND status = 'published'
                ORDER BY published_at DESC
                """,
                list(video_ids),
            )

        vid_items = []
        for p in video_posts:
            pid = p["id"]
            mp4_path = VIDEO_DIR / f"{pid}.mp4"
            if not mp4_path.exists():
                continue
            size = mp4_path.stat().st_size
            pub_date = p["published_at"].strftime("%a, %d %b %Y %H:%M:%S +0000") if p["published_at"] else now
            desc = escape_xml(p["excerpt"] or p["title"])
            vid_items.append(f"""    <item>
      <title><![CDATA[{p['title']}]]></title>
      <link>{SITE_URL}/posts/{p['slug']}</link>
      <description>{desc}</description>
      <guid>gladlabs-video-{pid}</guid>
      <pubDate>{pub_date}</pubDate>
      <enclosure url="{R2_URL}/video/{pid}.mp4" length="{size}" type="video/mp4" />
    </item>""")

        video_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Glad Labs Video</title>
    <link>{SITE_URL}</link>
    <description>AI development video essays from Glad Labs.</description>
    <language>en-us</language>
    <atom:link href="{SITE_URL}/video-feed.xml" rel="self" type="application/rss+xml" />
{chr(10).join(vid_items)}
  </channel>
</rss>"""

        vid_path = PUBLIC_DIR / "video-feed.xml"
        vid_path.write_text(video_xml, encoding="utf-8")
        print(f"Written {vid_path} ({len(vid_items)} episodes)")
    else:
        print("No video episodes found on disk")


if __name__ == "__main__":
    asyncio.run(main())
