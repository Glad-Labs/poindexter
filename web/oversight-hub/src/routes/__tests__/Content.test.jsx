/**
 * Content.jsx route tests
 *
 * Covers:
 * - Initial render: page title, loading indicator
 * - Content list renders with mocked API response
 * - Error state when API fails
 * - Tab filter changes the visible posts
 * - Search filters posts by title
 * - Empty state when no posts returned
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import Content from '../Content';

// ── mocks ─────────────────────────────────────────────────────────────────
const { mockGetPosts, mockUpdatePost, mockDeletePost } = vi.hoisted(() => ({
  mockGetPosts: vi.fn(),
  mockUpdatePost: vi.fn(),
  mockDeletePost: vi.fn(),
}));

vi.mock('../../lib/apiClient', () => ({
  getPosts: mockGetPosts,
  updatePost: mockUpdatePost,
  deletePost: mockDeletePost,
}));

vi.mock('../../services/errorLoggingService', () => ({
  logError: vi.fn(),
}));

vi.mock('../../components/modals/PostEditor', () => ({
  default: ({ post, onClose }) => (
    <div data-testid="post-editor">
      <span>Editing: {post?.title}</span>
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

// ── sample data ────────────────────────────────────────────────────────────
const SAMPLE_POSTS = [
  {
    id: '1',
    title: 'AI in Healthcare',
    status: 'published',
    excerpt: 'About AI healthcare',
    view_count: 100,
    slug: 'ai-in-healthcare',
  },
  {
    id: '2',
    title: 'Machine Learning Basics',
    status: 'draft',
    excerpt: 'ML fundamentals',
    view_count: 0,
    slug: 'ml-basics',
  },
  {
    id: '3',
    title: 'Deep Learning Overview',
    status: 'draft',
    excerpt: 'Neural network overview',
    view_count: 0,
    slug: 'deep-learning',
  },
];

function renderContent() {
  return render(
    <MemoryRouter>
      <Content />
    </MemoryRouter>
  );
}

// ── tests ──────────────────────────────────────────────────────────────────
describe('Content', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetPosts.mockResolvedValue(SAMPLE_POSTS);
    mockUpdatePost.mockResolvedValue({});
    mockDeletePost.mockResolvedValue({});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial render', () => {
    it('renders the page title', async () => {
      renderContent();
      await waitFor(() => {
        expect(screen.getByText('Content Library')).toBeInTheDocument();
      });
    });

    it('calls getPosts on mount', async () => {
      renderContent();
      await waitFor(() => {
        expect(mockGetPosts).toHaveBeenCalledTimes(1);
      });
    });

    it('shows all posts after loading', async () => {
      renderContent();
      await waitFor(() => {
        expect(screen.getByText('AI in Healthcare')).toBeInTheDocument();
        expect(screen.getByText('Machine Learning Basics')).toBeInTheDocument();
      });
    });
  });

  describe('error state', () => {
    it('shows error message when API fails', async () => {
      mockGetPosts.mockRejectedValue(new Error('Server error'));
      renderContent();
      await waitFor(() => {
        expect(screen.getByText(/Failed to load content/i)).toBeInTheDocument();
      });
    });
  });

  describe('empty state', () => {
    it('shows no posts message when API returns empty array', async () => {
      mockGetPosts.mockResolvedValue([]);
      renderContent();
      await waitFor(() => {
        expect(screen.getByText(/No content found/i)).toBeInTheDocument();
      });
    });
  });

  describe('search filter', () => {
    it('filters posts by search query', async () => {
      renderContent();
      await waitFor(() => {
        expect(screen.getByText('AI in Healthcare')).toBeInTheDocument();
      });

      const searchInput = screen.getByPlaceholderText(/Search content/i);
      fireEvent.change(searchInput, { target: { value: 'Machine' } });

      await waitFor(() => {
        expect(screen.getByText('Machine Learning Basics')).toBeInTheDocument();
        expect(screen.queryByText('AI in Healthcare')).not.toBeInTheDocument();
      });
    });
  });

  describe('tab filter', () => {
    it('filters to only show published posts when Published tab is clicked', async () => {
      renderContent();
      await waitFor(() => {
        expect(screen.getByText('AI in Healthcare')).toBeInTheDocument();
      });

      const publishedTab = screen.getByRole('button', { name: /Published/i });
      fireEvent.click(publishedTab);

      await waitFor(() => {
        expect(screen.getByText('AI in Healthcare')).toBeInTheDocument();
        expect(
          screen.queryByText('Machine Learning Basics')
        ).not.toBeInTheDocument();
      });
    });

    it('filters to only show draft posts when Drafts tab is clicked', async () => {
      renderContent();
      await waitFor(() => {
        expect(screen.getByText('AI in Healthcare')).toBeInTheDocument();
      });

      const draftsTab = screen.getByRole('button', { name: /Draft/i });
      fireEvent.click(draftsTab);

      await waitFor(() => {
        expect(screen.getByText('Machine Learning Basics')).toBeInTheDocument();
        expect(screen.queryByText('AI in Healthcare')).not.toBeInTheDocument();
      });
    });
  });

  describe('statistics', () => {
    it('shows correct total count', async () => {
      renderContent();
      await waitFor(() => {
        // 3 total posts
        expect(screen.getByText('3')).toBeInTheDocument();
      });
    });
  });
});
