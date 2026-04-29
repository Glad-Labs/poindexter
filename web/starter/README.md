# Poindexter Starter — Minimal Next.js Frontend

A deliberately small Next.js + Tailwind starter for a Poindexter-backed
publication. Fork it, restyle it, deploy it.

This is **not** the Glad Labs site. It's the un-branded skeleton every
fork-user would otherwise have to build from scratch.

---

## What you get

```
web/starter/
├── app/
│   ├── layout.tsx             # site shell (header + footer)
│   ├── page.tsx               # home — paginated post list
│   ├── posts/[slug]/page.tsx  # single-post view with OG metadata
│   ├── about/page.tsx         # placeholder about page
│   ├── not-found.tsx          # 404
│   └── globals.css            # article prose defaults
├── components/
│   ├── SiteHeader.tsx         # nav seam — your logo goes here
│   ├── SiteFooter.tsx
│   └── PostCard.tsx           # list row — restyle freely
├── lib/
│   └── api.ts                 # typed wrapper around /api/posts
├── next.config.js
├── tailwind.config.js
├── package.json
└── .env.example
```

16 files, no extra dependencies, one env var to configure.

---

## Run it

**Prerequisite:** the Poindexter backend is running and has at least one
published post. See the top-level `README.md` for backend setup.

```bash
cd web/starter
cp .env.example .env.local  # edit NEXT_PUBLIC_POINDEXTER_API_URL if needed
npm install
npm run dev                 # → http://localhost:3000
```

If the home page says "Cannot load posts", either the backend isn't
running at `http://localhost:8002` or it has no published posts yet.

---

## Customize

Everything worth changing is in one of four places.

### 1. Identity (name, description, metadata)

Edit `app/layout.tsx`. Update the `metadata` object (title, description,
OG defaults) and replace "Your Site Name" in `components/SiteHeader.tsx`.

### 2. Brand (colors, fonts)

Edit `tailwind.config.js` — the `colors.brand` block is where your
palette goes. Defaults are neutral greys + a generic blue accent so
nothing accidentally ships looking like someone else's brand.

For fonts, add a `<link>` tag in `app/layout.tsx` (or use `next/font`)
and update the `fontFamily.sans` stack in `tailwind.config.js`.

### 3. Layout

Add pages under `app/`. The App Router conventions apply (`page.tsx`,
`layout.tsx`, `loading.tsx`, etc.). Add nav items to
`components/SiteHeader.tsx`.

### 4. API

`lib/api.ts` is the thin wrapper. If you want to cache differently,
proxy through R2/CDN instead of hitting FastAPI directly, or add auth,
this is the one place to change.

The starter calls three backend endpoints:

- `GET /api/posts?limit=N&offset=M` — paginated list
- `GET /api/posts/{slug}` — single post with HTML content
- `GET /api/categories` — (stubbed, not used by default pages)

See `src/cofounder_agent/routes/cms_routes.py` in the main repo for the
full API surface.

---

## Deploy

### Vercel (easiest)

```bash
npx vercel
```

Set `NEXT_PUBLIC_POINDEXTER_API_URL` in the Vercel project's env vars.
The backend must be reachable from Vercel's build + serverless regions
(Tailscale Funnel, Cloudflare Tunnel, or a public host).

### Self-host (Docker)

```dockerfile
FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:20-slim
WORKDIR /app
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/node_modules ./node_modules
COPY package.json ./
EXPOSE 3000
CMD ["npm", "run", "start"]
```

---

## Notes

- **No auth.** The starter is read-only and talks to the public backend
  endpoints. If you add admin / preview flows, gate them with the
  `api_token` Bearer header that the backend checks (`middleware/api_token_auth.py`).
- **No analytics.** Drop in Plausible, Fathom, Umami, or your choice.
- **Image optimization.** The starter uses `<img>` for zero dependencies.
  Swap to `next/image` if you want Next's automatic optimization —
  `next.config.js` already whitelists `*.r2.dev` and `images.pexels.com`.
- **HTML content.** Posts are rendered with `dangerouslySetInnerHTML`
  because the backend emits sanitized HTML. If you prefer to parse
  markdown client-side, swap in `react-markdown` or `marked`.

---

## License

Same as the umbrella repo: Apache 2.0. See `LICENSE` at the repo root.
