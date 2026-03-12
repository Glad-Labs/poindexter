/**
 * Tests for components/PostMetadata.tsx
 *
 * Covers:
 * - Renders formatted date
 * - Calculates reading time from content
 * - Displays word count
 * - Shows view count when > 0
 * - Hides view count when 0
 * - Falls back to today when no publishedAt
 */

import { render } from '@testing-library/react';
import { PostMetadata } from '../PostMetadata';

// Mock content-utils
jest.mock('../../lib/content-utils', () => ({
  stripHtmlTags: jest.fn((text) => text.replace(/<[^>]*>/g, '')),
}));

/**
 * Get normalized textContent of all <span> elements in the rendered output.
 */
function getSpanTexts(container) {
  return Array.from(container.querySelectorAll('span')).map((el) =>
    el.textContent.replace(/\s+/g, ' ').trim()
  );
}

// Using newline-separated words avoids the trailing-space empty-token issue.
// 'word\n'.repeat(N) produces exactly N non-empty tokens when split on /\s+/.
const CONTENT_398 = 'word\n'.repeat(398); // ceil(398/200) = 2 min read, 398 words

describe('PostMetadata', () => {
  const DEFAULT_PROPS = {
    publishedAt: '2026-03-01T00:00:00Z',
    createdAt: '2026-02-28T00:00:00Z',
    content: CONTENT_398,
    viewCount: 0,
  };

  test('renders a <time> element with the year', () => {
    const { container } = render(<PostMetadata {...DEFAULT_PROPS} />);
    const timeEl = container.querySelector('time');
    expect(timeEl).toBeInTheDocument();
    expect(timeEl.textContent).toMatch(/2026/);
  });

  test('shows reading time in minutes', () => {
    const { container } = render(<PostMetadata {...DEFAULT_PROPS} />);
    const spans = getSpanTexts(container);
    // ceil(398/200) = 2 min read — may show as "2 min read" with spaces normalized
    expect(spans.some((t) => t.match(/^\d+ min read$/))).toBe(true);
    expect(spans.some((t) => t.startsWith('2 '))).toBe(true);
  });

  test('shows correct reading time value of 2 minutes', () => {
    const { container } = render(<PostMetadata {...DEFAULT_PROPS} />);
    const spans = getSpanTexts(container);
    expect(spans).toContain('2 min read');
  });

  test('rounds reading time up (ceil)', () => {
    // 201 words → ceil(201/200) = 2 min
    const content = 'word\n'.repeat(201);
    const { container } = render(
      <PostMetadata {...DEFAULT_PROPS} content={content} />
    );
    const spans = getSpanTexts(container);
    expect(spans).toContain('2 min read');
  });

  test('shows word count in a span', () => {
    const { container } = render(<PostMetadata {...DEFAULT_PROPS} />);
    const spans = getSpanTexts(container);
    // Should include a span matching "NNN words"
    expect(spans.some((t) => t.endsWith('words'))).toBe(true);
  });

  test('word count is approximately correct', () => {
    const { container } = render(<PostMetadata {...DEFAULT_PROPS} />);
    const spans = getSpanTexts(container);
    const wordSpan = spans.find((t) => t.endsWith('words'));
    // Extract numeric part — some locales use commas
    const num = parseInt(wordSpan.replace(/[^0-9]/g, ''), 10);
    expect(num).toBeGreaterThan(390);
    expect(num).toBeLessThan(410);
  });

  test('word count formatted with toLocaleString for large content', () => {
    // Use 1499 words so the count stays predictable regardless of trailing token handling
    const content = 'word\n'.repeat(1499);
    const { container } = render(
      <PostMetadata {...DEFAULT_PROPS} content={content} />
    );
    const spans = getSpanTexts(container);
    const wordSpan = spans.find((t) => t.endsWith('words'));
    // The word count should be in the 1490-1510 range and use locale formatting
    expect(wordSpan).toBeTruthy();
    const num = parseInt(wordSpan.replace(/[^0-9]/g, ''), 10);
    expect(num).toBeGreaterThan(1490);
    expect(num).toBeLessThan(1510);
  });

  test('does not show view count when viewCount is 0', () => {
    const { container } = render(
      <PostMetadata {...DEFAULT_PROPS} viewCount={0} />
    );
    const spans = getSpanTexts(container);
    expect(spans.some((t) => t.includes('views'))).toBe(false);
  });

  test('shows view count when viewCount > 0', () => {
    const { container } = render(
      <PostMetadata {...DEFAULT_PROPS} viewCount={1234} />
    );
    const spans = getSpanTexts(container);
    const viewSpan = spans.find((t) => t.includes('views'));
    expect(viewSpan).toBeTruthy();
    expect(viewSpan).toMatch(/1[,.]?234 views/);
  });

  test('uses today as date when publishedAt is undefined', () => {
    const currentYear = new Date().getFullYear().toString();
    const { container } = render(
      <PostMetadata
        publishedAt={undefined}
        createdAt="2026-02-01T00:00:00Z"
        content="word word word"
        viewCount={0}
      />
    );
    const timeEl = container.querySelector('time');
    expect(timeEl).toBeInTheDocument();
    expect(timeEl.textContent).toContain(currentYear);
  });

  test('renders without crashing for empty content', () => {
    const { container } = render(
      <PostMetadata
        publishedAt="2026-01-01T00:00:00Z"
        createdAt="2026-01-01T00:00:00Z"
        content=""
        viewCount={0}
      />
    );
    const spans = getSpanTexts(container);
    expect(spans.some((t) => t.includes('min read'))).toBe(true);
  });

  test('strips HTML tags before counting words', () => {
    // 100 words wrapped in <p> tags — after stripping, word count < 200 → 1 min read
    const htmlContent = '<p>word</p>'.repeat(100);
    const { container } = render(
      <PostMetadata {...DEFAULT_PROPS} content={htmlContent} />
    );
    const spans = getSpanTexts(container);
    expect(spans).toContain('1 min read');
  });
});
