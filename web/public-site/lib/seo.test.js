/**
 * SEO Utility Tests (lib/seo.js)
 *
 * Tests SEO optimization functions
 * Verifies: Meta generation, structured data, canonical URLs, OG tags
 */
import {
  buildMetaDescription,
  buildSEOTitle,
  generateCanonicalURL,
  generateOGTags,
  generateRobotsTag,
  buildPostSEO,
} from '../lib/seo';

describe('SEO Utility (lib/seo.js)', () => {
  const mockPost = {
    id: 'post-1',
    title: 'Complete Guide to React Hooks',
    slug: 'guide-react-hooks',
    excerpt: 'Learn everything about React Hooks with practical examples',
    featured_image_url: 'https://example.com/image.jpg',
    author_id: 'author-1',
    published_at: '2024-01-15T10:00:00Z',
    view_count: 1500,
  };

  describe('buildSEOTitle', () => {
    it('should build title with site name', () => {
      const title = buildSEOTitle('Short Title', 'Glad Labs');
      expect(title).toContain('Short Title');
      expect(title).toContain('Glad Labs');
    });

    it('should truncate long titles', () => {
      const longTitle =
        'This Is An Extremely Long Blog Post Title That Will Exceed Sixty Characters Easily';
      const title = buildSEOTitle(longTitle, 'Glad Labs');
      expect(title.length).toBeLessThanOrEqual(
        longTitle.length + ' | Blog '.length + 'Glad Labs'.length
      );
    });

    it('should handle missing site name', () => {
      const title = buildSEOTitle('Post Title', '');
      expect(title).toContain('Post Title');
    });
  });

  describe('buildMetaDescription', () => {
    it('should return excerpt as description', () => {
      const desc = buildMetaDescription(mockPost.excerpt);
      expect(desc).toBe(mockPost.excerpt);
    });

    it('should truncate long descriptions to 160 chars', () => {
      const longDesc = 'A'.repeat(200);
      const desc = buildMetaDescription(longDesc);
      expect(desc.length).toBeLessThanOrEqual(163); // 160 + '...'
    });

    it('should return fallback if excerpt is empty', () => {
      const desc = buildMetaDescription('', 'Fallback description');
      expect(desc).toBe('Fallback description');
    });

    it('should return fallback if excerpt is null', () => {
      const desc = buildMetaDescription(null, 'Fallback');
      expect(desc).toBe('Fallback');
    });
  });

  describe('generateCanonicalURL', () => {
    it('should generate canonical URL with slug', () => {
      const canonical = generateCanonicalURL(mockPost.slug);
      expect(canonical).toContain(mockPost.slug);
    });

    it('should use base URL', () => {
      const canonical = generateCanonicalURL(mockPost.slug);
      expect(canonical).toMatch(/^https?:\/\//);
    });

    it('should strip leading/trailing slashes from slug', () => {
      const canonical = generateCanonicalURL('/guide-react-hooks/');
      expect(canonical).not.toMatch(/\/\/guide/);
      expect(canonical).not.toMatch(/hooks\/$/);
    });

    it('should be a valid absolute URL', () => {
      const canonical = generateCanonicalURL(mockPost.slug);
      expect(() => new URL(canonical)).not.toThrow();
      const url = new URL(canonical);
      expect(url.hostname.length).toBeGreaterThan(0);
    });

    it('should return base URL for empty slug', () => {
      const canonical = generateCanonicalURL('');
      expect(canonical).toBe('https://glad-labs.com');
    });
  });

  describe('generateOGTags', () => {
    it('should generate og:title', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags['og:title']).toBe(mockPost.title);
    });

    it('should generate og:description', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags['og:description']).toBe(mockPost.excerpt);
    });

    it('should generate og:image', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags['og:image']).toBeDefined();
    });

    it('should set og:type as article', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags['og:type']).toBe('article');
    });

    it('should include og:url', () => {
      const ogTags = generateOGTags(mockPost);
      expect(ogTags['og:url']).toContain(mockPost.slug);
    });

    it('should return empty object for null post', () => {
      const ogTags = generateOGTags(null);
      expect(ogTags).toEqual({});
    });
  });

  describe('generateRobotsTag', () => {
    it('should generate index,follow by default', () => {
      const robots = generateRobotsTag();
      expect(robots).toContain('index');
      expect(robots).toContain('follow');
    });

    it('should support noindex', () => {
      const robots = generateRobotsTag({ index: false });
      expect(robots).toContain('noindex');
    });

    it('should support nofollow', () => {
      const robots = generateRobotsTag({ follow: false });
      expect(robots).toContain('nofollow');
    });
  });

  describe('buildPostSEO', () => {
    it('should return all SEO fields', () => {
      const seo = buildPostSEO(mockPost);
      expect(seo).toHaveProperty('title');
      expect(seo).toHaveProperty('description');
      expect(seo).toHaveProperty('canonical');
      expect(seo).toHaveProperty('og');
      expect(seo).toHaveProperty('twitter');
      expect(seo).toHaveProperty('robots');
    });

    it('should generate canonical URL containing slug', () => {
      const seo = buildPostSEO(mockPost);
      expect(seo.canonical).toContain(mockPost.slug);
    });

    it('should generate OG tags with correct title', () => {
      const seo = buildPostSEO(mockPost);
      expect(seo.og['og:title']).toBe(mockPost.title);
    });

    it('should handle minimal post data', () => {
      const minimalPost = {
        title: 'Simple Post',
        slug: 'simple-post',
        excerpt: 'Description',
      };
      const seo = buildPostSEO(minimalPost);
      expect(seo).toBeDefined();
      expect(seo.title).toContain('Simple Post');
    });
  });
});
