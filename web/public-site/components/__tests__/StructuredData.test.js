/**
 * Tests for components/StructuredData.tsx
 *
 * Covers:
 * - BreadcrumbSchema
 * - FAQSchema
 * - OrganizationSchema
 * - BlogPostingSchema
 * - NewsArticleSchema
 */

import { render } from '@testing-library/react';
import {
  BreadcrumbSchema,
  FAQSchema,
  OrganizationSchema,
  BlogPostingSchema,
  NewsArticleSchema,
} from '../StructuredData';

/**
 * Parse the JSON-LD from a rendered <script type="application/ld+json"> tag.
 */
function parseSchema(container) {
  const script = container.querySelector('script[type="application/ld+json"]');
  expect(script).not.toBeNull();
  return JSON.parse(script.innerHTML);
}

// ---------------------------------------------------------------------------
// BreadcrumbSchema
// ---------------------------------------------------------------------------

describe('BreadcrumbSchema', () => {
  test('renders a script tag with application/ld+json', () => {
    const { container } = render(
      <BreadcrumbSchema items={[{ label: 'Home', url: '/' }]} />
    );
    const script = container.querySelector(
      'script[type="application/ld+json"]'
    );
    expect(script).not.toBeNull();
  });

  test('schema type is BreadcrumbList', () => {
    const { container } = render(
      <BreadcrumbSchema items={[{ label: 'Blog', url: '/blog' }]} />
    );
    expect(parseSchema(container)['@type']).toBe('BreadcrumbList');
  });

  test('schema context is https://schema.org', () => {
    const { container } = render(<BreadcrumbSchema items={[]} />);
    expect(parseSchema(container)['@context']).toBe('https://schema.org');
  });

  test('itemListElement count matches items', () => {
    const items = [
      { label: 'Home', url: '/' },
      { label: 'Blog', url: '/blog' },
      { label: 'Post', url: '/post' },
    ];
    const { container } = render(<BreadcrumbSchema items={items} />);
    expect(parseSchema(container).itemListElement).toHaveLength(3);
  });

  test('positions are 1-indexed', () => {
    const items = [
      { label: 'Home', url: '/' },
      { label: 'Blog', url: '/blog' },
    ];
    const { container } = render(<BreadcrumbSchema items={items} />);
    const schema = parseSchema(container);
    expect(schema.itemListElement[0].position).toBe(1);
    expect(schema.itemListElement[1].position).toBe(2);
  });

  test('item URLs are prefixed with site domain', () => {
    const { container } = render(
      <BreadcrumbSchema items={[{ label: 'Blog', url: '/blog' }]} />
    );
    const schema = parseSchema(container);
    expect(schema.itemListElement[0].item).toContain('/blog');
    expect(schema.itemListElement[0].item).toContain('www.gladlabs.io');
  });

  test('item names match labels', () => {
    const { container } = render(
      <BreadcrumbSchema items={[{ label: 'AI News', url: '/ai' }]} />
    );
    expect(parseSchema(container).itemListElement[0].name).toBe('AI News');
  });

  test('empty items array produces empty itemListElement', () => {
    const { container } = render(<BreadcrumbSchema items={[]} />);
    expect(parseSchema(container).itemListElement).toHaveLength(0);
  });

  test('renders with no items prop (default)', () => {
    const { container } = render(<BreadcrumbSchema />);
    expect(parseSchema(container).itemListElement).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// FAQSchema
// ---------------------------------------------------------------------------

describe('FAQSchema', () => {
  const faqs = [
    { question: 'What is AI?', answer: 'Artificial Intelligence.' },
    { question: 'What is ML?', answer: 'Machine Learning.' },
  ];

  test('schema type is FAQPage', () => {
    const { container } = render(<FAQSchema faqs={faqs} />);
    expect(parseSchema(container)['@type']).toBe('FAQPage');
  });

  test('mainEntity count matches faqs', () => {
    const { container } = render(<FAQSchema faqs={faqs} />);
    expect(parseSchema(container).mainEntity).toHaveLength(2);
  });

  test('each mainEntity item is type Question', () => {
    const { container } = render(<FAQSchema faqs={faqs} />);
    const schema = parseSchema(container);
    expect(schema.mainEntity[0]['@type']).toBe('Question');
  });

  test('question name matches input', () => {
    const { container } = render(<FAQSchema faqs={faqs} />);
    expect(parseSchema(container).mainEntity[0].name).toBe('What is AI?');
  });

  test('acceptedAnswer type is Answer', () => {
    const { container } = render(<FAQSchema faqs={faqs} />);
    expect(parseSchema(container).mainEntity[0].acceptedAnswer['@type']).toBe(
      'Answer'
    );
  });

  test('acceptedAnswer text matches input', () => {
    const { container } = render(<FAQSchema faqs={faqs} />);
    expect(parseSchema(container).mainEntity[0].acceptedAnswer.text).toBe(
      'Artificial Intelligence.'
    );
  });

  test('empty faqs produces empty mainEntity', () => {
    const { container } = render(<FAQSchema faqs={[]} />);
    expect(parseSchema(container).mainEntity).toHaveLength(0);
  });

  test('renders with no faqs prop (default)', () => {
    const { container } = render(<FAQSchema />);
    expect(parseSchema(container).mainEntity).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// OrganizationSchema
// ---------------------------------------------------------------------------

describe('OrganizationSchema', () => {
  test('schema type is Organization', () => {
    const { container } = render(<OrganizationSchema />);
    expect(parseSchema(container)['@type']).toBe('Organization');
  });

  test('name is Glad Labs', () => {
    const { container } = render(<OrganizationSchema />);
    expect(parseSchema(container).name).toBe('Glad Labs');
  });

  test('url is https://www.gladlabs.io', () => {
    const { container } = render(<OrganizationSchema />);
    expect(parseSchema(container).url).toBe('https://www.gladlabs.io');
  });

  test('sameAs is an array with social profiles', () => {
    const { container } = render(<OrganizationSchema />);
    const schema = parseSchema(container);
    expect(Array.isArray(schema.sameAs)).toBe(true);
    expect(schema.sameAs.length).toBeGreaterThan(0);
  });

  test('contactPoint is present with email', () => {
    const { container } = render(<OrganizationSchema />);
    const schema = parseSchema(container);
    expect(schema.contactPoint).toBeDefined();
    expect(schema.contactPoint.email).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// BlogPostingSchema
// ---------------------------------------------------------------------------

describe('BlogPostingSchema', () => {
  const props = {
    headline: 'AI in 2026',
    description: 'A look at AI trends.',
    image: 'https://www.gladlabs.io/ai.jpg',
    datePublished: '2026-03-01',
    dateModified: '2026-03-05',
    author: 'Test Author',
  };

  test('schema type is BlogPosting', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container)['@type']).toBe('BlogPosting');
  });

  test('headline matches prop', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).headline).toBe(props.headline);
  });

  test('description matches prop', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).description).toBe(props.description);
  });

  test('image matches prop', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).image).toBe(props.image);
  });

  test('datePublished matches prop', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).datePublished).toBe(props.datePublished);
  });

  test('dateModified matches prop', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).dateModified).toBe(props.dateModified);
  });

  test('dateModified defaults to datePublished when not provided', () => {
    const { container } = render(
      <BlogPostingSchema
        headline="x"
        description="y"
        image="z"
        datePublished="2026-01-01"
      />
    );
    expect(parseSchema(container).dateModified).toBe('2026-01-01');
  });

  test('author name matches prop', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).author.name).toBe(props.author);
  });

  test('author defaults to Glad Labs', () => {
    const { container } = render(
      <BlogPostingSchema
        headline="x"
        description="y"
        image="z"
        datePublished="2026-01-01"
      />
    );
    expect(parseSchema(container).author.name).toBe('Glad Labs');
  });

  test('publisher type is Organization', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).publisher['@type']).toBe('Organization');
  });

  test('publisher name is Glad Labs', () => {
    const { container } = render(<BlogPostingSchema {...props} />);
    expect(parseSchema(container).publisher.name).toBe('Glad Labs');
  });
});

// ---------------------------------------------------------------------------
// NewsArticleSchema
// ---------------------------------------------------------------------------

describe('NewsArticleSchema', () => {
  const props = {
    headline: 'Breaking AI News',
    description: 'Latest developments.',
    image: 'https://www.gladlabs.io/news.jpg',
    datePublished: '2026-03-10',
  };

  test('schema type is NewsArticle', () => {
    const { container } = render(<NewsArticleSchema {...props} />);
    expect(parseSchema(container)['@type']).toBe('NewsArticle');
  });

  test('headline matches prop', () => {
    const { container } = render(<NewsArticleSchema {...props} />);
    expect(parseSchema(container).headline).toBe(props.headline);
  });

  test('dateModified defaults to datePublished when not provided', () => {
    const { container } = render(<NewsArticleSchema {...props} />);
    expect(parseSchema(container).dateModified).toBe(props.datePublished);
  });

  test('publisher type is Organization', () => {
    const { container } = render(<NewsArticleSchema {...props} />);
    expect(parseSchema(container).publisher['@type']).toBe('Organization');
  });
});
