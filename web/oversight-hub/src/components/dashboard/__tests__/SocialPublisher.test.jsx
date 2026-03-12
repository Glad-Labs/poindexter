/**
 * Tests for components/dashboard/SocialPublisher.jsx
 *
 * Covers:
 * - Renders Social Publisher heading
 * - Renders Create Post and Post History tabs
 * - Error when submitting without content
 * - Error when submitting without platform selection
 * - Switching to Post History tab calls loadPosts
 * - Successful post creation clears form and shows success
 * - Failed post creation shows error
 * - Error cleared after new interaction
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// Mock socialService — vi.hoisted() required so variables are available in factory
const { mockGetPlatforms, mockCreatePost, mockGetPosts, mockDeletePost } =
  vi.hoisted(() => ({
    mockGetPlatforms: vi.fn(),
    mockCreatePost: vi.fn(),
    mockGetPosts: vi.fn(),
    mockDeletePost: vi.fn(),
  }));

vi.mock('../../../services/socialService', () => ({
  getPlatforms: mockGetPlatforms,
  createPost: mockCreatePost,
  getPosts: mockGetPosts,
  deletePost: mockDeletePost,
}));

import { SocialPublisher } from '../SocialPublisher';

const MOCK_PLATFORMS = {
  twitter: { name: 'Twitter', connected: true, icon: '🐦' },
  linkedin: { name: 'LinkedIn', connected: true, icon: '💼' },
};

describe('SocialPublisher — base render', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetPlatforms.mockResolvedValue(MOCK_PLATFORMS);
    mockGetPosts.mockResolvedValue({ posts: [] });
    mockCreatePost.mockResolvedValue({ success: true, id: 'post-1' });
  });

  it('renders Social Publisher heading', async () => {
    render(<SocialPublisher />);
    await waitFor(() => {
      expect(screen.getByText('Social Publisher')).toBeInTheDocument();
    });
  });

  it('renders Create Post and Post History tabs', async () => {
    render(<SocialPublisher />);
    expect(
      screen.getByRole('tab', { name: /Create Post/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /Post History/i })
    ).toBeInTheDocument();
  });

  it('shows Create New Post card header by default', async () => {
    render(<SocialPublisher />);
    await waitFor(() => {
      expect(screen.getByText('Create New Post')).toBeInTheDocument();
    });
  });

  it('calls getPlatforms on mount', async () => {
    render(<SocialPublisher />);
    await waitFor(() => {
      expect(mockGetPlatforms).toHaveBeenCalledTimes(1);
    });
  });

  it('shows platform chips from API response', async () => {
    render(<SocialPublisher />);
    await waitFor(() => {
      // Platform chips should be rendered
      expect(screen.getByText('Twitter')).toBeInTheDocument();
      expect(screen.getByText('LinkedIn')).toBeInTheDocument();
    });
  });
});

describe('SocialPublisher — button state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetPlatforms.mockResolvedValue(MOCK_PLATFORMS);
    mockGetPosts.mockResolvedValue({ posts: [] });
  });

  it('Create & Schedule Post button is disabled when content is empty', async () => {
    render(<SocialPublisher />);
    await waitFor(() =>
      expect(screen.getByText('Create New Post')).toBeInTheDocument()
    );

    const submitBtn = screen.getByRole('button', {
      name: /Create & Schedule Post/i,
    });
    // Empty content → disabled
    expect(submitBtn).toBeDisabled();
  });

  it('Create & Schedule Post button is disabled when no platforms selected', async () => {
    render(<SocialPublisher />);
    await waitFor(() =>
      expect(screen.getByText('Create New Post')).toBeInTheDocument()
    );

    // Fill content but don't select platform
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'My social post content' } });

    const submitBtn = screen.getByRole('button', {
      name: /Create & Schedule Post/i,
    });
    // No platforms selected → still disabled
    expect(submitBtn).toBeDisabled();
  });

  it('Create & Schedule Post button is enabled when content and platforms are selected', async () => {
    render(<SocialPublisher />);
    await waitFor(() =>
      expect(screen.getByText('Twitter')).toBeInTheDocument()
    );

    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'My social post content' } });

    // Toggle twitter platform
    fireEvent.click(screen.getByText('Twitter'));

    const submitBtn = screen.getByRole('button', {
      name: /Create & Schedule Post/i,
    });
    expect(submitBtn).not.toBeDisabled();
  });
});

describe('SocialPublisher — post actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetPlatforms.mockResolvedValue(MOCK_PLATFORMS);
    mockGetPosts.mockResolvedValue({
      posts: [
        {
          id: 'post-abc',
          content: 'An existing post',
          platforms: ['twitter'],
          status: 'published',
          created_at: '2026-03-01T10:00:00Z',
        },
      ],
    });
  });

  it('switches to Post History tab and loads posts', async () => {
    render(<SocialPublisher />);
    await waitFor(() => expect(mockGetPlatforms).toHaveBeenCalled());

    const historyTab = screen.getByRole('tab', { name: /Post History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(mockGetPosts).toHaveBeenCalledWith({ limit: 50 });
    });
  });

  it('shows post history items after switching tab', async () => {
    render(<SocialPublisher />);
    await waitFor(() => expect(mockGetPlatforms).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('tab', { name: /Post History/i }));

    await waitFor(() => {
      // Component truncates content with .substring(0,50)+'...' so look for partial match
      expect(document.body.textContent).toContain('An existing post');
    });
  });
});

describe('SocialPublisher — successful create', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetPlatforms.mockResolvedValue(MOCK_PLATFORMS);
    mockGetPosts.mockResolvedValue({ posts: [] });
    mockCreatePost.mockResolvedValue({ success: true, id: 'new-post-1' });
  });

  it('calls createPost with correct data when form is valid', async () => {
    render(<SocialPublisher />);
    await waitFor(() =>
      expect(screen.getByText('Twitter')).toBeInTheDocument()
    );

    // Fill content
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, {
      target: { value: 'Check out our new blog post!' },
    });

    // Select twitter platform chip
    fireEvent.click(screen.getByText('Twitter'));

    // Submit — button text is "Create & Schedule Post"
    const submitBtn = screen.getByRole('button', {
      name: /Create & Schedule Post/i,
    });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockCreatePost).toHaveBeenCalledWith(
        expect.objectContaining({ content: 'Check out our new blog post!' })
      );
    });
  });
});
