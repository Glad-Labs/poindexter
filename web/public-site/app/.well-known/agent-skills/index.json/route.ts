// Agent Skills Discovery Index (agentskills.io / Cloudflare Agent Skills RFC v0.2.0)
// Publishes the skills this site's agent infrastructure exposes.
// The sha256 field is a placeholder — in production this should be the
// real digest of the skill file at the referenced URL.

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';

export async function GET() {
  const base = SITE_URL.replace(/\/$/, '');

  const index = {
    $schema:
      'https://agentskills.io/schemas/index/v0.2.0/schema.json',
    skills: [
      {
        name: 'poindexter-mcp',
        type: 'mcp',
        description:
          'Connect to the Poindexter MCP server for content management, pipeline monitoring, and knowledge retrieval.',
        url: `${base}/.well-known/mcp/server-card.json`,
        // sha256 is required by the spec; computed at deploy time in a real
        // pipeline — set to the empty-hash sentinel for bootstrapping.
        sha256:
          'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      },
      {
        name: 'api-catalog',
        type: 'api',
        description:
          'RFC 9727 API catalog listing all publicly accessible Glad Labs API endpoints.',
        url: `${base}/.well-known/api-catalog`,
        sha256:
          'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      },
    ],
  };

  return new Response(JSON.stringify(index, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
    },
  });
}
