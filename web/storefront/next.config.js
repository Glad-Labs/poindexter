/** @type {import('next').NextConfig} */
const nextConfig = {
  // Pull in the @glad-labs/brand workspace package — its JSX exports need
  // Next's SWC to transpile them, and workspace-linked packages aren't in
  // next's default transpile allowlist.
  transpilePackages: ['@glad-labs/brand'],
  reactStrictMode: true,
};

export default nextConfig;
