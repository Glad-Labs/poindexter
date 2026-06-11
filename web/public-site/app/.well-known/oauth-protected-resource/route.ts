// RFC 9728 — OAuth 2.0 Protected Resource Metadata
// Tells agents how to obtain access tokens for protected Glad Labs APIs.
//
// The Poindexter MCP server uses OAuth 2.1 Client Credentials Grant.
// Agents wanting MCP access must register an OAuth client via auth.md,
// then obtain a token from the authorization server listed here.

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';

export async function GET() {
  const base = SITE_URL.replace(/\/$/, '');

  const metadata = {
    resource: base,
    // The Poindexter backend is the authorization server.
    // For public API access, the newsletter and posts endpoints are open (no token required).
    // The MCP server and operator APIs require Client Credentials tokens.
    authorization_servers: [
      // Poindexter backend — operator-configured; not publicly hosted.
      // MCP clients register via the instructions in /auth.md.
      'https://api.gladlabs.io',
    ],
    // Scopes are defined by the Poindexter OAuth 2.1 server.
    // Standard scopes for MCP and API consumers:
    scopes_supported: ['mcp:read', 'mcp:write', 'content:read'],
    bearer_methods_supported: ['header'],
    resource_signing_alg_values_supported: ['RS256'],
    resource_documentation: `${base}/auth.md`,
  };

  return new Response(JSON.stringify(metadata, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
    },
  });
}
