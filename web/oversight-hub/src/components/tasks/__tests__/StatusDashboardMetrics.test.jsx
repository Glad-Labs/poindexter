/**
 * Tests for StatusDashboardMetrics component in StatusComponents.jsx
 *
 * Covers:
 * - Loading state while fetching metrics
 * - Error state when getMetrics fails
 * - No metrics state
 * - Renders Status Distribution heading
 * - Status chips and counts displayed
 * - Average processing time shown
 * - Success rate shown
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock unifiedStatusService — vi.hoisted() required
const { mockGetMetrics } = vi.hoisted(() => ({
  mockGetMetrics: vi.fn(),
}));

vi.mock('../../../services/unifiedStatusService', () => ({
  unifiedStatusService: {
    getMetrics: mockGetMetrics,
    getHistory: vi.fn().mockResolvedValue([]),
    getFailures: vi.fn().mockResolvedValue([]),
  },
}));

// Mock statusEnums
vi.mock('../../../Constants/statusEnums', () => ({
  STATUS_COLORS: {
    pending: '#ff9800',
    completed: '#4caf50',
    failed: '#f44336',
    running: '#2196f3',
  },
  getStatusLabel: vi.fn((s) => s),
}));

import { StatusDashboardMetrics } from '../StatusComponents';

const MOCK_METRICS = {
  status_distribution: {
    pending: 5,
    completed: 42,
    failed: 3,
    running: 2,
  },
  average_processing_time: 1800, // 1800 seconds (but component divides by 60 = 30 "seconds" display)
  success_rate: 0.933,
  total_tasks: 52,
};

describe('StatusDashboardMetrics — loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading indicator initially', () => {
    mockGetMetrics.mockImplementation(() => new Promise(() => {}));
    render(<StatusDashboardMetrics />);
    // CircularProgress renders role="progressbar"
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows error message when getMetrics fails', async () => {
    mockGetMetrics.mockRejectedValue(new Error('Service unavailable'));
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText(/Service unavailable/i)).toBeInTheDocument();
    });
  });

  it('shows no metrics message when getMetrics returns null', async () => {
    mockGetMetrics.mockResolvedValue(null);
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText(/No metrics available/i)).toBeInTheDocument();
    });
  });
});

describe('StatusDashboardMetrics — data loaded', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetMetrics.mockResolvedValue(MOCK_METRICS);
  });

  it('renders Status Distribution heading', async () => {
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('Status Distribution')).toBeInTheDocument();
    });
  });

  it('renders status names from distribution', async () => {
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('pending')).toBeInTheDocument();
      expect(screen.getByText('completed')).toBeInTheDocument();
      expect(screen.getByText('failed')).toBeInTheDocument();
    });
  });

  it('renders status counts', async () => {
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument(); // completed count
      expect(screen.getByText('5')).toBeInTheDocument(); // pending count
    });
  });

  it('renders Average Processing Time section', async () => {
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('Average Processing Time')).toBeInTheDocument();
    });
  });

  it('renders Success Rate section', async () => {
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('Success Rate')).toBeInTheDocument();
    });
  });

  it('renders success rate value', async () => {
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      // 0.933 * 100 = 93.3%
      expect(screen.getByText('93.3%')).toBeInTheDocument();
    });
  });
});
