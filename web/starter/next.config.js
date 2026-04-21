/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow loading post images from the default Poindexter R2 bucket
  // pattern. Fork-users: replace with your own image host(s).
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: '**.r2.dev' },
      { protocol: 'https', hostname: 'images.pexels.com' },
    ],
  },
  // Poindexter's backend runs on :8002 by default. Override in .env.local
  // if yours lives elsewhere (Docker, remote host, Tailscale, etc.).
  env: {
    NEXT_PUBLIC_POINDEXTER_API_URL:
      process.env.NEXT_PUBLIC_POINDEXTER_API_URL || 'http://localhost:8002',
  },
};

module.exports = nextConfig;
