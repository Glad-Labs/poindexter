// MCP Server Card (SEP-1649 / modelcontextprotocol#2127)
// Describes the Poindexter MCP server capabilities for automated discovery.
// Agents can use this to understand what tools and resources are available
// without requiring prior configuration.

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';

export async function GET() {
  const base = SITE_URL.replace(/\/$/, '');

  const serverCard = {
    $schema: 'https://modelcontextprotocol.io/schemas/server-card/v1',
    serverInfo: {
      name: 'Poindexter',
      version: '1.0.0',
      description:
        'AI-operated content pipeline and business OS. Provides tools for content management, pipeline monitoring, system settings, and knowledge retrieval.',
      vendor: 'Glad Labs, LLC',
      homepage: base,
      documentation: 'https://gladlabs.mintlify.app',
      license: 'Apache-2.0',
    },
    // MCP transport — operators connect via stdio or SSE after provisioning
    // OAuth 2.1 Client Credentials (see /auth.md for registration).
    transport: {
      type: 'stdio',
      note: 'Poindexter MCP runs as a local process. Remote SSE transport is planned.',
    },
    capabilities: {
      tools: true,
      resources: false,
      prompts: false,
      logging: true,
    },
    // High-level capability summary — full tool list requires auth.
    toolCategories: [
      'content-management',
      'pipeline-monitoring',
      'system-settings',
      'memory-search',
      'approval-workflow',
      'observability',
    ],
    authentication: {
      type: 'oauth2',
      grant: 'client_credentials',
      registration: `${base}/auth.md`,
      protected_resource: `${base}/.well-known/oauth-protected-resource`,
    },
    contact: {
      email: 'hello@gladlabs.io',
    },
  };

  return new Response(JSON.stringify(serverCard, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
    },
  });
}
