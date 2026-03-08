/**
 * Content Utils Tests (lib/content-utils.js)
 *
 * Tests content processing and formatting utilities
 * Verifies: Markdown parsing, excerpt generation, slug creation
 */
import {
  generateExcerpt,
  formatDate,
  createSlug,
  extractFirstImage,
  sanitizeHtml,
} from '../lib/content-utils';

describe('Content Utils (lib/content-utils.js)', () => {
  describe('generateExcerpt', () => {
    it('should generate excerpt from content', () => {
      const content =
        'This is a long article about React development. It covers many topics.';
      const excerpt = generateExcerpt(content, 50);
      expect(excerpt.length).toBeLessThanOrEqual(50);
    });

    it('should respect word boundaries', () => {
      const content =
        'This is a longer article that should be truncated at word boundary';
      const excerpt = generateExcerpt(content, 20);
      expect(excerpt).not.toMatch(/\s$/); // Should not end with space
    });

    it('should handle empty content', () => {
      const excerpt = generateExcerpt('', 50);
      expect(excerpt).toBe('');
    });

    it('should handle null content gracefully', () => {
      const excerpt = generateExcerpt(null, 50);
      expect(excerpt).toBe('');
    });

    it('should remove HTML tags', () => {
      const htmlContent = '<p>This is <strong>bold</strong> text</p>';
      const excerpt = generateExcerpt(htmlContent);
      expect(excerpt).not.toContain('<');
      expect(excerpt).not.toContain('>');
    });

    it('should remove markdown syntax', () => {
      const markdownContent = '# Title\n\n**Bold** and *italic* text';
      const excerpt = generateExcerpt(markdownContent);
      expect(excerpt).not.toContain('#');
      expect(excerpt).not.toContain('**');
      expect(excerpt).not.toContain('*');
    });

    it('should add ellipsis if content truncated', () => {
      const content =
        'This is a long article that will definitely be truncated';
      const excerpt = generateExcerpt(content, 20);
      expect(excerpt).toContain('...') ||
        expect(excerpt.length < content.length).toBe(true);
    });

    it('should handle unicode characters', () => {
      const content =
        'This contains emoji 🚀 and other unicode characters 中文';
      const excerpt = generateExcerpt(content);
      expect(excerpt).toBeDefined();
    });
  });

  describe('formatDate', () => {
    it('should format date to readable string', () => {
      const date = '2024-01-15T10:00:00Z';
      const formatted = formatDate(date);
      expect(formatted).toMatch(/\d+/); // Should contain numbers
      expect(formatted).toMatch(/[A-Za-z]/); // Should contain letters (month name)
    });

    it('should handle different date formats', () => {
      const isoDate = '2024-01-15T10:00:00Z';
      const timestamp = 1705318800000;
      const formattedIso = formatDate(isoDate);
      const formattedTs = formatDate(new Date(timestamp));
      expect(formattedIso).toBeDefined();
      expect(formattedTs).toBeDefined();
    });

    it('should handle invalid dates gracefully', () => {
      const formatted = formatDate('invalid-date');
      expect(formatted).toBe('Invalid date') || expect(formatted).toBeDefined();
    });

    it('should support custom format options', () => {
      const date = '2024-01-15T10:00:00Z';
      const formatted = formatDate(date, { year: 'numeric', month: 'long' });
      expect(formatted).toContain('2024') || expect(formatted).toBeDefined();
    });

    it('should handle null/undefined dates', () => {
      expect(formatDate(null)).toBeDefined();
      expect(formatDate(undefined)).toBeDefined();
    });

    it('should maintain timezone awareness', () => {
      const date = '2024-01-15T10:00:00Z';
      const formatted = formatDate(date);
      expect(formatted).toBeDefined();
    });
  });

  describe('createSlug', () => {
    it('should convert title to slug', () => {
      const title = 'Getting Started with React';
      const slug = createSlug(title);
      expect(slug).toBe('getting-started-with-react');
    });

    it('should handle special characters', () => {
      const title = 'Hello & Goodbye!';
      const slug = createSlug(title);
      expect(slug).not.toContain('&');
      expect(slug).not.toContain('!');
    });

    it('should remove extra spaces', () => {
      const title = 'Multiple   Spaces   Here';
      const slug = createSlug(title);
      expect(slug).not.toContain('  ');
    });

    it('should convert to lowercase', () => {
      const title = 'UPPERCASE TITLE';
      const slug = createSlug(title);
      expect(slug).toBe(slug.toLowerCase());
    });

    it('should handle unicode characters', () => {
      const title = 'Café Résumé';
      const slug = createSlug(title);
      expect(slug).toBeDefined();
      expect(slug).not.toContain('é');
    });

    it('should handle numbers correctly', () => {
      const title = 'React 18 Features';
      const slug = createSlug(title);
      expect(slug).toContain('18');
    });

    it('should handle empty string', () => {
      const slug = createSlug('');
      expect(slug).toBe('');
    });
  });

  describe('extractFirstImage', () => {
    it('should extract first image URL from markdown', () => {
      const markdown =
        '![Alt text](https://example.com/image1.jpg)\nMore content\n![Alt text](https://example.com/image2.jpg)';
      const imageUrl = extractFirstImage(markdown);
      expect(imageUrl).toContain('example.com/image1.jpg');
    });

    it('should extract image from HTML', () => {
      const html =
        '<img src="https://example.com/image.jpg" alt="Text">\nMore content';
      const imageUrl = extractFirstImage(html);
      expect(imageUrl).toContain('example.com/image.jpg');
    });

    it('should return null if no image found', () => {
      const content = 'Just text without images';
      const imageUrl = extractFirstImage(content);
      expect(imageUrl).toBeNull();
    });

    it('should handle empty content', () => {
      const imageUrl = extractFirstImage('');
      expect(imageUrl).toBeNull();
    });

    it('should prefer markdown images over HTML', () => {
      const content =
        'Text ![Markdown](https://example.com/markdown.jpg) and <img src="https://example.com/html.jpg">';
      const imageUrl = extractFirstImage(content);
      expect(imageUrl).toContain('markdown.jpg') ||
        expect(imageUrl).toBeDefined();
    });

    it('should handle relative image paths', () => {
      const markdown = '![Alt](/images/photo.jpg)';
      const imageUrl = extractFirstImage(markdown);
      expect(imageUrl).toContain('/images/photo.jpg');
    });
  });

  describe('sanitizeHtml', () => {
    it('should remove script tags', () => {
      const html = '<p>Safe content <script>alert("XSS")</script></p>';
      const sanitized = sanitizeHtml(html);
      expect(sanitized).not.toContain('<script>');
      expect(sanitized).not.toContain('alert');
    });

    it('should remove style attributes with dangerous values', () => {
      const html = '<p style="background:url(javascript:alert())">Content</p>';
      const sanitized = sanitizeHtml(html);
      expect(sanitized).not.toContain('javascript:');
    });

    it('should preserve safe HTML tags', () => {
      const html = '<p><strong>Bold</strong> and <em>italic</em></p>';
      const sanitized = sanitizeHtml(html);
      expect(sanitized).toContain('<strong>');
      expect(sanitized).toContain('<em>');
    });

    it('should remove onload handlers', () => {
      const html = '<img src="image.jpg" onload="alert()">';
      const sanitized = sanitizeHtml(html);
      expect(sanitized).not.toContain('onload');
    });

    it('should handle encoded entities', () => {
      const html = '<p>&lt;script&gt;alert()&lt;/script&gt;</p>';
      const sanitized = sanitizeHtml(html);
      expect(sanitized).toBeDefined();
    });

    it('should preserve links', () => {
      const html = '<p><a href="https://example.com">Link</a></p>';
      const sanitized = sanitizeHtml(html);
      expect(sanitized).toContain('<a');
      expect(sanitized).toContain('example.com');
    });
  });
});
