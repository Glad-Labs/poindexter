/**
 * /llms.txt — a plain-text index of the site for language models
 * (https://llmstxt.org): what the site covers, then every main-feed post
 * newest first, each linking to its Markdown version (/posts/<slug>.md, served
 * by proxy.ts) with a one-line description.
 *
 * Dev diary entries are left out, as they are from the sitemap: they are
 * noindexed status logs, not articles. The description is the post's
 * answer-first "In brief" summary when it has one (services/post_summaries.py:
 * whole sentences, every number checked against the post), else its
 * seo_description, which the static export stores cut at a fixed length and
 * often mid-word ("fits your own hardware se"); a cut one is trimmed back to
 * its last whole word and marked with an ellipsis.
 */
import { cleanPostTitle, sortPostsNewestFirst } from './posts';

export interface LlmsPost {
  title: string;
  slug: string;
  summary?: string | null;
  seo_description?: string;
  excerpt?: string;
  niche_slug?: string;
  published_at?: string;
  created_at?: string;
}

export interface LlmsSite {
  name: string;
  url: string;
  tagline: string;
  summary: string;
}

// Measured on the live index (2026-10-18): 71 of 136 main-feed descriptions end
// mid-sentence, all but one at 148-160 characters, some mid-word ("rollo") and
// some on a whole word; the text cannot tell which. One this long that does not
// end a sentence was cut, so its last word goes and an ellipsis marks the cut.
const CUT_AT = 140;

export function oneLine(text?: string): string {
  const flat = (text || '').replace(/\s+/g, ' ').trim();
  if (flat.length < CUT_AT || /[.!?)"'”]$/.test(flat)) return flat;
  const lastSpace = flat.lastIndexOf(' ');
  const whole = lastSpace > 0 ? flat.slice(0, lastSpace) : flat;
  return `${whole.replace(/[\s,;:–—-]+$/, '')}…`;
}

export function buildLlmsTxt(posts: LlmsPost[], site: LlmsSite): string {
  const base = site.url.replace(/\/$/, '');
  const articles = sortPostsNewestFirst(
    posts.filter((p) => p.slug && p.niche_slug !== 'dev_diary')
  ).map((p) => {
    const desc = oneLine(p.summary || p.seo_description || p.excerpt);
    const link = `- [${cleanPostTitle(p.title)}](${base}/posts/${p.slug}.md)`;
    return desc ? `${link}: ${desc}` : link;
  });
  return [
    `# ${site.name}`,
    '',
    `> ${site.tagline}`,
    '',
    site.summary,
    '',
    '## Articles',
    '',
    ...articles,
    '',
    '## Optional',
    '',
    `- [About](${base}/about): who writes this and how`,
    `- [RSS feed](${base}/feed.xml): every new post`,
    '',
  ].join('\n');
}
