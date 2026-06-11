# Auth.md — Glad Labs Agent Registration

This document describes how AI agents and automated systems can authenticate
with and interact with the Glad Labs platform.

## Public APIs (No Authentication Required)

The following endpoints are publicly accessible without any credentials:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/posts` | GET | Paginated blog post index |
| `/api/posts/{slug}` | GET | Individual post content |
| `/api/newsletter/subscribe` | POST | Newsletter subscription |
| `/feed.xml` | GET | RSS feed (blog posts) |
| `/podcast-feed.xml` | GET | RSS feed (podcast episodes) |
| `/sitemap.xml` | GET | Full site sitemap |

## MCP Server Access (Operator Authentication Required)

The Poindexter MCP server exposes tools for content management, pipeline
monitoring, and knowledge retrieval. Access requires OAuth 2.1 Client
Credentials.

### Registration

MCP access is operator-provisioned. Contact [hello@gladlabs.io](mailto:hello@gladlabs.io)
to request an OAuth client for your agent.

Operators self-provision via the Poindexter CLI:

```bash
poindexter auth migrate-mcp
```

### Token Endpoint

```
POST {issuer}/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id={client_id}
&client_secret={client_secret}
&scope=mcp:read mcp:write
```

### Supported Grant Types

- `client_credentials` — for server-to-server / agent access

### Supported Scopes

| Scope | Description |
|-------|-------------|
| `mcp:read` | Read-only MCP tool access |
| `mcp:write` | Full MCP tool access including mutations |
| `content:read` | Read published content and pipeline state |

## Content Licensing

Content published on gladlabs.io is copyright Glad Labs, LLC.

- **Search indexing**: Permitted
- **AI training**: Not permitted without explicit license agreement
- **AI input / RAG**: Not permitted without explicit license agreement

See `robots.txt` for the machine-readable declaration (Content-Signal directives).

## Discovery Resources

| Resource | URL |
|----------|-----|
| API Catalog | `/.well-known/api-catalog` |
| MCP Server Card | `/.well-known/mcp/server-card.json` |
| OAuth Protected Resource | `/.well-known/oauth-protected-resource` |
| Agent Skills Index | `/.well-known/agent-skills/index.json` |

## Contact

- **General**: hello@gladlabs.io
- **Privacy**: privacy@gladlabs.io
- **Documentation**: https://gladlabs.mintlify.app
