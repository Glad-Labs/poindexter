/**
 * /llms.txt (lib/llms-txt.ts): the site summary, then every main-feed post
 * newest first, linking to its Markdown version with a one-line description.
 */
import { buildLlmsTxt, oneLine } from '../../lib/llms-txt';

const SITE = {
  name: 'Glad Labs',
  url: 'https://www.gladlabs.io/',
  tagline: 'AI and hardware',
  summary: 'One developer.',
};

describe('oneLine', () => {
  test('a description cut at the length limit loses its last word and gains an ellipsis', () => {
    // A real one from the index, cut at 160 characters in the middle of "rollouts".
    const cut =
      'GRPO training spends most of its time generating rollouts, so the trick is to keep the inference weights on the machine generating rollo';
    const padded = `${cut.padStart(150, 'x')}`;
    expect(oneLine(padded)).toBe(
      `${padded.slice(0, padded.lastIndexOf(' '))}…`
    );
    expect(oneLine(padded).endsWith('generating…')).toBe(true);
  });

  test('a short or finished description is kept as written, on one line', () => {
    expect(oneLine('Fast.\n  Really fast.')).toBe('Fast. Really fast.');
    const finished = 'x'.repeat(160) + ' and done.';
    expect(oneLine(finished)).toBe(finished);
    expect(oneLine(undefined)).toBe('');
  });
});

describe('buildLlmsTxt', () => {
  const posts = [
    {
      title: 'Older Post',
      slug: 'older',
      seo_description: 'Older one.',
      published_at: '2026-09-01T00:00:00Z',
    },
    {
      title: 'Dev Diary 42',
      slug: 'diary',
      seo_description: 'Status.',
      niche_slug: 'dev_diary',
      published_at: '2026-10-10T00:00:00Z',
    },
    {
      title: 'Newer Post',
      slug: 'newer',
      excerpt: 'From the excerpt.',
      published_at: '2026-10-01T00:00:00Z',
    },
  ];
  const text = buildLlmsTxt(posts, SITE);

  test('opens with the site name, tagline and summary', () => {
    expect(
      text.startsWith(
        '# Glad Labs\n\n> AI and hardware\n\nOne developer.\n\n## Articles\n'
      )
    ).toBe(true);
  });

  test('lists articles newest first, linking the markdown version', () => {
    const lines = text.split('\n').filter((l) => l.startsWith('- ['));
    expect(lines[0]).toBe(
      '- [Newer Post](https://www.gladlabs.io/posts/newer.md): From the excerpt.'
    );
    expect(lines[1]).toBe(
      '- [Older Post](https://www.gladlabs.io/posts/older.md): Older one.'
    );
  });

  test('leaves the dev diary out, as the sitemap does', () => {
    expect(text).not.toContain('diary');
  });

  test('ends with the optional links', () => {
    expect(text).toContain(
      '## Optional\n\n- [About](https://www.gladlabs.io/about)'
    );
  });
});

describe('the article line prefers the In brief summary', () => {
  const post = {
    title: 'Best GPUs',
    slug: 'best-gpus',
    seo_description: 'Learn which GPU fits your own hardware se',
    published_at: '2026-10-10T00:00:00Z',
  };

  test('a post with a summary is described by it, whole', () => {
    const summary =
      'The RTX 5090 (32GB) is the best overall card for local LLMs. A used RTX 3090 is the best value.';
    const text = buildLlmsTxt([{ ...post, summary }], SITE);
    expect(text).toContain(
      `- [Best GPUs](https://www.gladlabs.io/posts/best-gpus.md): ${summary}`
    );
  });

  test('a post without one falls back to its seo description', () => {
    const text = buildLlmsTxt([{ ...post, summary: null }], SITE);
    expect(text).toContain(': Learn which GPU fits your own hardware se');
  });
});
