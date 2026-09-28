import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from '@playwright/test';
import { STATIC_URL } from '../lib/static-url';

/**
 * Tag archive page E2E coverage.
 *
 * Covers: /tag/[slug]
 *
 * Acceptance criteria (issue #377):
 * - A tag page loads without a 500 error and renders a heading
 * - A tag that posts carry lists those posts, not the empty state
 * - A tag no post carries shows the empty state and lists no posts
 *
 * A tag page filters the R2 static post index (lib/posts.ts
 * getAllPublishedPosts), not the FastAPI API, so this spec needs no backend
 * and has no gate that skips without one. See author.spec.ts for the gate it
 * used to have. The spec reads the same index itself to pick its known tag.
 */

// The known tag is picked from live data when the spec runs: it is the tag
// carried by the most posts in the post index. The page lists the posts in
// that same index that carry its tag, so this tag has posts by construction.
// Content drift can change which tag is picked, but not whether it has posts.
//
// It used to be a hardcoded slug ('ai-ml'), and the list test accepted "posts
// OR the empty state" so that content drift could never fail it. That also
// passed a broken page: with R2 unreachable, /tag/ai-ml rendered "Nothing
// tagged #ai-ml yet." and all five tests were green (#4170). It would have
// passed just the same if the static export stopped writing `tags` into
// posts/index.json. The same either-or shape in author.spec.ts was green while
// every author page was broken (#3339).

// The index is found the way the page finds it. STATIC_URL comes from
// lib/static-url.js, which lib/posts.ts reads too, so this spec holds no bucket
// of its own. (It is not imported from lib/posts.ts, which pulls in
// @sentry/nextjs.) The scheduled run sets no override, so it reads the index
// production reads: on 2026-09-28 the live /tag/ai-ml and /tag/indie-hacking
// listed exactly the posts it tags with them. If you point a dev server at
// another bucket, export NEXT_PUBLIC_STATIC_URL to this spec as well. Should
// the two ever disagree, the spec fails: either it cannot read the index, or
// it picks a tag the page has no posts for. It cannot pass a broken page,
// because what passes is still what the page shows.

const UNKNOWN_TAG_SLUG = 'this-tag-definitely-does-not-exist-xyz123';

// The two states of the page's post list, matched on the markup it renders.
// A post card is a brand <Card> (a div.gl-card), not an <article>, so a post
// is found by its title link to /posts/<slug>. The empty state is a Card too,
// so neither `article` nor `.gl-card` can tell the two states apart.
const POST_LINKS = 'a[href^="/posts/"]';
const EMPTY_STATE = /Nothing tagged #/i;

/** The fields of a posts/index.json entry this spec reads. */
interface IndexedPost {
  slug?: unknown;
  tags?: unknown;
}

interface KnownTag {
  tag: string;
  path: string;
  /** Slugs of the posts the index tags with `tag`. */
  postSlugs: string[];
}

/**
 * The tag that the most posts in the R2 post index carry, read when the spec
 * runs. Fails, never skips, when there is no such tag: an index in which no
 * post carries a tag leaves every tag page empty.
 */
async function mostUsedTag(request: APIRequestContext): Promise<KnownTag> {
  const url = `${STATIC_URL}/posts/index.json`;
  const response = await request.get(url);
  expect(response.status(), `GET ${url}`).toBe(200);
  const posts: IndexedPost[] = (await response.json()).posts ?? [];

  // The page matches a tag case-insensitively, so count it the same way.
  const slugsByTag = new Map<string, Set<string>>();
  for (const { slug, tags } of posts) {
    if (typeof slug !== 'string' || !Array.isArray(tags)) continue;
    for (const tag of tags) {
      if (typeof tag !== 'string' || !tag) continue;
      const key = tag.toLowerCase();
      const slugs = slugsByTag.get(key) ?? new Set<string>();
      slugs.add(slug);
      slugsByTag.set(key, slugs);
    }
  }

  // Most posts first. A tie goes to the alphabetically first tag, so the pick
  // only moves when the content does.
  const [best] = [...slugsByTag].sort(
    ([tagA, a], [tagB, b]) => b.size - a.size || (tagA < tagB ? -1 : 1)
  );
  if (!best) {
    throw new Error(
      `None of the ${posts.length} posts in ${url} carries a tag, so every ` +
        'tag page is empty. Did the static export stop writing `tags`?'
    );
  }
  const [tag, slugs] = best;
  return {
    tag,
    path: `/tag/${encodeURIComponent(tag)}`,
    postSlugs: [...slugs],
  };
}

/** A selector for the link to any one of these posts. */
function linkToAnyOf(slugs: string[]) {
  return slugs
    .map((slug) => `a[href="/posts/${slug.replace(/["\\]/g, '\\$&')}"]`)
    .join(', ');
}

/**
 * The section that holds the page's post list, found by its level-2 heading:
 * "Articles tagged <tag>" (visually hidden) above the post cards, or "Nothing
 * tagged #<tag> yet." in the empty state. Scoping to it keeps the header's
 * "← All articles" button and the site chrome from standing in for either
 * state.
 */
function postList(page: Page) {
  return page.locator('section').filter({
    has: page.getByRole('heading', {
      level: 2,
      name: /^(Articles tagged|Nothing tagged)/i,
    }),
  });
}

test.describe('Tag Archive Page', () => {
  test.describe('the most-used tag', () => {
    let known: KnownTag;

    test.beforeAll(async ({ request }) => {
      known = await mostUsedTag(request);
    });

    test('loads without a 404 or 500', async ({ page }) => {
      const response = await page.goto(known.path);
      expect(response?.status()).not.toBe(500);
      expect(response?.status()).not.toBe(404);
    });

    test('renders without an Internal Server Error', async ({ page }) => {
      await page.goto(known.path);
      await expect(page.locator('body')).toBeVisible();
      await expect(
        page.locator('text=Internal Server Error')
      ).not.toBeVisible();
    });

    test('renders a heading', async ({ page }) => {
      await page.goto(known.path);
      const heading = page.locator('h1, h2').first();
      await expect(heading).toBeVisible();
    });

    // This tag's posts are in the index the page filters, so an empty state
    // here is a bug: the site cannot reach R2, the index has lost its `tags`,
    // or the page's filter is broken.
    test('lists the posts that carry it, not the empty state', async ({
      page,
    }) => {
      await page.goto(known.path);
      const list = postList(page);
      const count = known.postSlugs.length;
      await expect(
        list.locator(POST_LINKS).first(),
        `${known.path} lists no posts, but ${count} in the index carry "${known.tag}"`
      ).toBeVisible();
      await expect(
        list.getByText(EMPTY_STATE),
        `${known.path} shows the empty state`
      ).toHaveCount(0);
      // Post links alone could be some other list, such as a grid of recent
      // posts under a reworded empty state. At least one must be a post the
      // index tags with this tag. Any one rather than all, because the page's
      // cached render can trail the index by a publish.
      await expect(
        list.locator(linkToAnyOf(known.postSlugs)).first(),
        `${known.path} lists posts, but none of the ${count} that carry "${known.tag}"`
      ).toBeVisible();
    });
  });

  test.describe('an unknown tag', () => {
    // The empty state belongs here. The no-posts check also pins that an
    // unknown tag never falls back to listing other posts.
    test('shows the empty state and lists no posts', async ({ page }) => {
      await page.goto(`/tag/${UNKNOWN_TAG_SLUG}`);
      const list = postList(page);
      await expect(list.getByText(EMPTY_STATE)).toBeVisible();
      await expect(list.locator(POST_LINKS)).toHaveCount(0);
    });

    test('renders without an Internal Server Error', async ({ page }) => {
      await page.goto(`/tag/${UNKNOWN_TAG_SLUG}`);
      await expect(
        page.locator('text=Internal Server Error')
      ).not.toBeVisible();
    });
  });
});
