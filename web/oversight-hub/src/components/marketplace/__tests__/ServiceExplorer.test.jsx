/**
 * Tests for components/marketplace/ServiceExplorer.jsx
 *
 * Covers:
 * - Loading spinner while fetching
 * - Error shown when API fails
 * - Renders Service Explorer heading
 * - Shows services list in sidebar
 * - Clicking a service loads its details
 * - Service detail tabs: Overview, Actions, Use Cases
 * - Empty state when no services
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// Mock capabilityService — vi.hoisted() required
const { mockListServices, mockGetServiceMetadata } = vi.hoisted(() => ({
  mockListServices: vi.fn(),
  mockGetServiceMetadata: vi.fn(),
}));

vi.mock('../../../services/capabilityService', () => ({
  getServiceRegistry: vi.fn(),
  listServices: mockListServices,
  getServiceMetadata: mockGetServiceMetadata,
}));

import { ServiceExplorer } from '../ServiceExplorer';

const MOCK_SERVICES = ['content_agent', 'financial_agent', 'market_agent'];

const MOCK_SERVICE_DETAILS = {
  name: 'content_agent',
  description: 'AI-powered content generation service',
  version: '2.0',
  actions: [
    {
      name: 'generate_blog_post',
      description: 'Generate a blog post from topic',
    },
    { name: 'analyze_seo', description: 'Analyze SEO for content' },
  ],
  use_cases: ['Blog automation', 'Content marketing'],
  capabilities: ['text_generation', 'seo_analysis'],
};

describe('ServiceExplorer — loading states', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading spinner initially', () => {
    mockListServices.mockImplementation(() => new Promise(() => {}));
    render(<ServiceExplorer />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows error alert when listServices fails', async () => {
    mockListServices.mockRejectedValue(new Error('Network error'));
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load services/i)).toBeInTheDocument();
    });
  });
});

describe('ServiceExplorer — services loaded', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListServices.mockResolvedValue(MOCK_SERVICES);
    mockGetServiceMetadata.mockResolvedValue(MOCK_SERVICE_DETAILS);
  });

  it('renders Service Explorer heading', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(screen.getByText('Service Explorer')).toBeInTheDocument();
    });
  });

  it('renders all services in the sidebar list', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(screen.getByText('content_agent')).toBeInTheDocument();
      expect(screen.getByText('financial_agent')).toBeInTheDocument();
      expect(screen.getByText('market_agent')).toBeInTheDocument();
    });
  });

  it('shows Services (N) count in sidebar header', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(
        screen.getByText(`Services (${MOCK_SERVICES.length})`)
      ).toBeInTheDocument();
    });
  });

  it('auto-selects first service and loads its details', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(mockGetServiceMetadata).toHaveBeenCalledWith('content_agent');
    });
  });

  it('shows service description in detail panel', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      // Description appears in CardHeader subheader and possibly Overview tab — use getAllByText
      const matches = screen.getAllByText(
        'AI-powered content generation service'
      );
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  it('shows action count chip', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(screen.getByText('2 Actions')).toBeInTheDocument();
    });
  });

  it('renders Overview, Actions, Use Cases tabs', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(
        screen.getByRole('tab', { name: /Overview/i })
      ).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /Actions/i })).toBeInTheDocument();
      expect(
        screen.getByRole('tab', { name: /Use Cases/i })
      ).toBeInTheDocument();
    });
  });

  it('clicking Actions tab shows action names', async () => {
    render(<ServiceExplorer />);
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Actions/i })).toBeInTheDocument()
    );

    fireEvent.click(screen.getByRole('tab', { name: /Actions/i }));

    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });
  });

  it('clicking a different service loads that service details', async () => {
    mockGetServiceMetadata.mockResolvedValueOnce(MOCK_SERVICE_DETAILS); // first call (content_agent auto-select)
    mockGetServiceMetadata.mockResolvedValueOnce({
      ...MOCK_SERVICE_DETAILS,
      name: 'financial_agent',
      description: 'Financial analysis service',
    });

    render(<ServiceExplorer />);
    await waitFor(() =>
      expect(screen.getByText('financial_agent')).toBeInTheDocument()
    );

    fireEvent.click(screen.getByText('financial_agent'));

    await waitFor(() => {
      expect(mockGetServiceMetadata).toHaveBeenCalledWith('financial_agent');
    });
  });
});

describe('ServiceExplorer — empty state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListServices.mockResolvedValue([]);
    mockGetServiceMetadata.mockResolvedValue(null);
  });

  it('renders header even when no services', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(screen.getByText('Service Explorer')).toBeInTheDocument();
    });
  });

  it('shows Services (0) in sidebar', async () => {
    render(<ServiceExplorer />);
    await waitFor(() => {
      expect(screen.getByText('Services (0)')).toBeInTheDocument();
    });
  });
});
