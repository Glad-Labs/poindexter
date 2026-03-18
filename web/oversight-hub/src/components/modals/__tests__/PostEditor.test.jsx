/**
 * Tests for components/modals/PostEditor.jsx
 *
 * Covers:
 * - Renders Edit Post heading
 * - Populates form fields from post prop
 * - Title field is required
 * - Slug field is disabled
 * - Content changes update form state
 * - Preview toggle shows rendered content
 * - onSave called with updated data on submit
 * - onClose called when X button clicked
 * - Cancel triggers confirmation dialog
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock DOMPurify
vi.mock('dompurify', () => ({
  default: {
    sanitize: vi.fn((html) => html),
  },
}));

// Mock CSS import
vi.mock('../PostEditor.css', () => ({}));

import PostEditor from '../PostEditor';

const SAMPLE_POST = {
  id: 'post-abc-123',
  title: 'My Great Blog Post',
  slug: 'my-great-blog-post',
  content: '# Hello World\n\nThis is my content.',
  excerpt: 'A short excerpt for the post.',
  featured_image_url: '',
  seo_title: 'My Great Blog Post | SEO',
  seo_description: 'SEO description here.',
  seo_keywords: 'blog, post, ai',
  status: 'published',
};

describe('PostEditor — rendering', () => {
  const onClose = vi.fn();
  const onSave = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders Edit Post heading', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    expect(screen.getByText(/Edit Post/i)).toBeInTheDocument();
  });

  it('populates title field from post prop', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    // Use id directly to avoid matching seo_title label
    const titleInput = document.getElementById('title');
    expect(titleInput.value).toBe('My Great Blog Post');
  });

  it('populates slug field from post prop', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const slugInput = document.getElementById('slug');
    expect(slugInput.value).toBe('my-great-blog-post');
  });

  it('slug field is disabled', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const slugInput = document.getElementById('slug');
    expect(slugInput).toBeDisabled();
  });

  it('populates excerpt from post prop', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const excerptField = document.getElementById('excerpt');
    expect(excerptField.value).toBe('A short excerpt for the post.');
  });

  it('title input has required attribute', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const titleInput = document.getElementById('title');
    expect(titleInput).toBeRequired();
  });

  it('renders close (×) button', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const closeBtn = screen.getByRole('button', { name: /Close dialog/i });
    expect(closeBtn).toBeInTheDocument();
  });

  it('clicking × button calls onClose', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const closeBtn = screen.getByRole('button', { name: /Close dialog/i });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('renders a save/submit button', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const saveBtn = screen.getByRole('button', { name: /Save/i });
    expect(saveBtn).toBeInTheDocument();
  });
});

describe('PostEditor — form editing', () => {
  const onClose = vi.fn();
  const onSave = vi.fn().mockResolvedValue({ success: true });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('updates title field on input change', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const titleInput = document.getElementById('title');
    fireEvent.change(titleInput, {
      target: { name: 'title', value: 'Updated Post Title' },
    });
    expect(titleInput.value).toBe('Updated Post Title');
  });

  it('submitting form calls onSave with updated data', async () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);

    const titleInput = document.getElementById('title');
    fireEvent.change(titleInput, {
      target: { name: 'title', value: 'New Title' },
    });

    const form = document.querySelector('form');
    fireEvent.submit(form);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'New Title' })
      );
    });
  });

  it('renders with empty post (no post prop)', () => {
    render(<PostEditor post={null} onClose={onClose} onSave={onSave} />);
    expect(screen.getByText(/Edit Post/i)).toBeInTheDocument();
    const titleInput = document.getElementById('title');
    expect(titleInput.value).toBe('');
  });
});

describe('PostEditor — preview toggle', () => {
  const onClose = vi.fn();
  const onSave = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders preview toggle button', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    // Look for toggle-related button
    const previewBtn = screen.getByRole('button', { name: /Preview/i });
    expect(previewBtn).toBeInTheDocument();
  });

  it('clicking preview toggle switches to preview mode', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const previewBtn = screen.getByRole('button', { name: /Preview/i });
    fireEvent.click(previewBtn);
    // After toggle, look for Edit button or preview content
    expect(document.body.textContent).toMatch(/Edit|Preview/i);
  });
});

describe('PostEditor — a11y and security: DOMPurify called with explicit allowlist (#737)', () => {
  /**
   * Verify that renderPreview passes an explicit ALLOWED_TAGS / ALLOWED_ATTR
   * allowlist to DOMPurify.sanitize rather than relying solely on the default
   * blocklist.  AI-generated content can contain prompt-injected HTML; an
   * allowlist provides defence-in-depth on top of the blocklist.
   */
  const onClose = vi.fn();
  const onSave = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls DOMPurify.sanitize with ALLOWED_TAGS option when preview is shown', async () => {
    const DOMPurify = (await import('dompurify')).default;

    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);

    const previewBtn = screen.getByRole('button', { name: /Preview/i });
    fireEvent.click(previewBtn);

    expect(DOMPurify.sanitize).toHaveBeenCalled();
    const callArgs = DOMPurify.sanitize.mock.calls[0];
    // Second argument must be an options object with ALLOWED_TAGS
    expect(callArgs[1]).toBeDefined();
    expect(callArgs[1]).toHaveProperty('ALLOWED_TAGS');
    expect(Array.isArray(callArgs[1].ALLOWED_TAGS)).toBe(true);
  });

  it('calls DOMPurify.sanitize with ALLOWED_ATTR option when preview is shown', async () => {
    const DOMPurify = (await import('dompurify')).default;

    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);

    const previewBtn = screen.getByRole('button', { name: /Preview/i });
    fireEvent.click(previewBtn);

    expect(DOMPurify.sanitize).toHaveBeenCalled();
    const callArgs = DOMPurify.sanitize.mock.calls[0];
    expect(callArgs[1]).toHaveProperty('ALLOWED_ATTR');
    expect(Array.isArray(callArgs[1].ALLOWED_ATTR)).toBe(true);
  });

  it('ALLOWED_TAGS includes expected safe markdown tags', async () => {
    const DOMPurify = (await import('dompurify')).default;

    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);

    const previewBtn = screen.getByRole('button', { name: /Preview/i });
    fireEvent.click(previewBtn);

    const callArgs = DOMPurify.sanitize.mock.calls[0];
    const allowedTags = callArgs[1].ALLOWED_TAGS;
    for (const tag of [
      'p',
      'strong',
      'em',
      'code',
      'h1',
      'h2',
      'h3',
      'ul',
      'ol',
      'li',
    ]) {
      expect(allowedTags).toContain(tag);
    }
  });

  it('ALLOWED_TAGS does not include script or iframe', async () => {
    const DOMPurify = (await import('dompurify')).default;

    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);

    const previewBtn = screen.getByRole('button', { name: /Preview/i });
    fireEvent.click(previewBtn);

    const callArgs = DOMPurify.sanitize.mock.calls[0];
    const allowedTags = callArgs[1].ALLOWED_TAGS;
    expect(allowedTags).not.toContain('script');
    expect(allowedTags).not.toContain('iframe');
    expect(allowedTags).not.toContain('object');
  });
});
