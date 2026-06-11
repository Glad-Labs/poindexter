'use client';

// WebMCP — exposes site tools to AI agents via the browser's nascent
// navigator.modelContext API (https://webmachinelearning.github.io/webmcp/).
// This is an early proposal; the runtime guard ensures nothing breaks in
// browsers that don't support it yet.

import { useEffect } from 'react';

const SITE_URL =
  typeof window !== 'undefined'
    ? window.location.origin
    : (process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io');

export default function WebMCP() {
  useEffect(() => {
    if (!('modelContext' in navigator)) return;
    const mc = navigator.modelContext;
    if (!mc || typeof mc.provideContext !== 'function') return;

    mc.provideContext({
      tools: [
        {
          name: 'list_posts',
          description:
            'Retrieve a paginated list of published blog posts from Glad Labs. Returns post titles, slugs, excerpts, tags, and publication dates.',
          inputSchema: {
            type: 'object',
            properties: {
              offset: {
                type: 'integer',
                description: 'Number of posts to skip (default 0)',
                default: 0,
              },
              limit: {
                type: 'integer',
                description: 'Maximum number of posts to return (default 10, max 50)',
                default: 10,
              },
            },
          },
          execute: async ({ offset = 0, limit = 10 } = {}) => {
            const url = `${SITE_URL}/api/posts?offset=${offset}&limit=${Math.min(limit, 50)}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`Posts API returned ${res.status}`);
            return res.json();
          },
        },
        {
          name: 'get_post',
          description:
            'Retrieve the full content of a specific Glad Labs blog post by its slug.',
          inputSchema: {
            type: 'object',
            properties: {
              slug: {
                type: 'string',
                description: 'The post slug (URL path segment after /posts/)',
              },
            },
            required: ['slug'],
          },
          execute: async ({ slug }) => {
            const res = await fetch(
              `${SITE_URL}/api/posts/${encodeURIComponent(slug)}`
            );
            if (!res.ok) throw new Error(`Post not found: ${slug}`);
            return res.json();
          },
        },
        {
          name: 'subscribe_newsletter',
          description:
            'Subscribe an email address to the Glad Labs newsletter.',
          inputSchema: {
            type: 'object',
            properties: {
              email: {
                type: 'string',
                format: 'email',
                description: 'Email address to subscribe',
              },
              first_name: {
                type: 'string',
                description: 'Subscriber first name (optional)',
              },
            },
            required: ['email'],
          },
          execute: async ({ email, first_name } = {}) => {
            const res = await fetch(`${SITE_URL}/api/newsletter/subscribe`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ email, first_name }),
            });
            if (!res.ok) throw new Error(`Subscribe failed: ${res.status}`);
            return res.json();
          },
        },
      ],
    });
  }, []);

  return null;
}
