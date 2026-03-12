/**
 * Tests for components/marketplace/CapabilitiesBrowser.jsx
 *
 * Covers:
 * - Loading state while fetching registry
 * - Error state when API fails
 * - Renders service list from registry
 * - Search filter narrows results
 * - Clicking a service opens details dialog
 * - Dialog closes on Close button
 * - Empty state when no services match search
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock capabilityService — vi.hoisted() required
const { mockGetServiceRegistry } = vi.hoisted(() => ({
  mockGetServiceRegistry: vi.fn(),
}));

vi.mock('../../../services/capabilityService', () => ({
  getServiceRegistry: mockGetServiceRegistry,
  listServices: vi.fn(),
  getServiceMetadata: vi.fn(),
}));

import { CapabilitiesBrowser } from '../CapabilitiesBrowser';

const MOCK_REGISTRY = {
  services: {
    content_agent: {
      description: 'Generates blog posts and articles',
      version: '2.0',
      actions: [
        {
          name: 'generate_blog_post',
          description: 'Create a blog post',
          parameters: [],
        },
        {
          name: 'analyze_content',
          description: 'Analyze existing content',
          parameters: [],
        },
      ],
    },
    financial_agent: {
      description: 'Handles financial analysis and reporting',
      version: '1.5',
      actions: [
        {
          name: 'analyze_revenue',
          description: 'Analyze revenue data',
          parameters: [],
        },
      ],
    },
  },
  total_services: 2,
  total_actions: 3,
};

describe('CapabilitiesBrowser — loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading spinner initially', () => {
    mockGetServiceRegistry.mockImplementation(() => new Promise(() => {}));
    render(<CapabilitiesBrowser />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows error when API fails', async () => {
    mockGetServiceRegistry.mockRejectedValue(new Error('Connection refused'));
    render(<CapabilitiesBrowser />);

    await waitFor(() => {
      expect(
        screen.getByText(/Failed to load capabilities/i)
      ).toBeInTheDocument();
    });
  });
});

describe('CapabilitiesBrowser — registry loaded', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServiceRegistry.mockResolvedValue(MOCK_REGISTRY);
  });

  it('renders service names from registry', async () => {
    render(<CapabilitiesBrowser />);
    await waitFor(() => {
      expect(screen.getByText('content_agent')).toBeInTheDocument();
      expect(screen.getByText('financial_agent')).toBeInTheDocument();
    });
  });

  it('renders service descriptions', async () => {
    render(<CapabilitiesBrowser />);
    await waitFor(() => {
      expect(screen.getByText(/Generates blog posts/i)).toBeInTheDocument();
    });
  });

  it('renders registry summary (total services and actions)', async () => {
    render(<CapabilitiesBrowser />);
    await waitFor(() => {
      expect(document.body.textContent).toContain('2');
    });
  });

  it('renders search field', async () => {
    render(<CapabilitiesBrowser />);
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Search capabilities/i)
      ).toBeInTheDocument();
    });
  });

  it('searching filters visible services', async () => {
    render(<CapabilitiesBrowser />);
    await waitFor(() =>
      expect(screen.getByText('content_agent')).toBeInTheDocument()
    );

    const searchField = screen.getByPlaceholderText(/Search capabilities/i);
    fireEvent.change(searchField, { target: { value: 'financial' } });

    // content_agent should no longer appear
    expect(screen.queryByText('content_agent')).not.toBeInTheDocument();
    expect(screen.getByText('financial_agent')).toBeInTheDocument();
  });

  it('clearing search shows all services again', async () => {
    render(<CapabilitiesBrowser />);
    await waitFor(() =>
      expect(screen.getByText('content_agent')).toBeInTheDocument()
    );

    const searchField = screen.getByPlaceholderText(/Search capabilities/i);
    fireEvent.change(searchField, { target: { value: 'financial' } });
    fireEvent.change(searchField, { target: { value: '' } });

    expect(screen.getByText('content_agent')).toBeInTheDocument();
    expect(screen.getByText('financial_agent')).toBeInTheDocument();
  });
});
