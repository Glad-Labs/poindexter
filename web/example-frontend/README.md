# Example Frontend

A minimal single-page blog that reads from the Glad Labs Engine static JSON export.

**Zero dependencies. Zero build step. Just HTML + JavaScript.**

## Quick Start

1. Edit `index.html` — change `STATIC_URL` to your R2/S3 bucket URL
2. Open `index.html` in a browser (or serve with any static file server)
3. Your AI-generated posts appear automatically

## How It Works

The engine pushes static JSON to your storage bucket on every publish:

```
your-bucket/static/posts/index.json    → all posts (metadata)
your-bucket/static/posts/{slug}.json   → individual post (with HTML content)
```

This frontend simply fetches those JSON files and renders them. No API server, no build step, no framework.

## Customize

This is a starting point. Replace it with whatever frontend you prefer:

- **Next.js** — SSG/ISR with `fetch()` from static JSON
- **Astro** — static site generation
- **Hugo** — fastest static builds
- **React/Vue/Svelte** — SPA reading from JSON
- **Anything** — the data is just JSON files on a CDN

## Deploy

Upload `index.html` to any static host:

- Cloudflare Pages (free)
- GitHub Pages (free)
- Netlify (free)
- Any S3 bucket with static hosting
- Or just open the file locally
