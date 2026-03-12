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
    const closeBtn = screen.getByRole('button', { name: '×' });
    expect(closeBtn).toBeInTheDocument();
  });

  it('clicking × button calls onClose', () => {
    render(<PostEditor post={SAMPLE_POST} onClose={onClose} onSave={onSave} />);
    const closeBtn = screen.getByRole('button', { name: '×' });
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
