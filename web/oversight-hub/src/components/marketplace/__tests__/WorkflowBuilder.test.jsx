/**
 * Tests for components/marketplace/WorkflowBuilder.jsx
 *
 * Covers:
 * - Loading state while data fetches
 * - Error state when API fails
 * - Renders Workflow Builder & Monitor heading
 * - Tab navigation: Workflow History, Statistics, Performance
 * - Empty state when no executions
 * - Workflow executions shown in table
 * - Refresh button triggers reload
 * - Statistics tab shows stat data
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock workflowManagementService — vi.hoisted() required
const {
  mockGetWorkflowHistory,
  mockGetExecutionDetails,
  mockGetWorkflowStatistics,
  mockGetPerformanceMetrics,
} = vi.hoisted(() => ({
  mockGetWorkflowHistory: vi.fn(),
  mockGetExecutionDetails: vi.fn(),
  mockGetWorkflowStatistics: vi.fn(),
  mockGetPerformanceMetrics: vi.fn(),
}));

vi.mock('../../../services/workflowManagementService', () => ({
  getWorkflowHistory: mockGetWorkflowHistory,
  getExecutionDetails: mockGetExecutionDetails,
  getWorkflowStatistics: mockGetWorkflowStatistics,
  getPerformanceMetrics: mockGetPerformanceMetrics,
}));

import { WorkflowBuilder } from '../WorkflowBuilder';

const MOCK_WORKFLOW_HISTORY = {
  executions: [
    {
      id: 'exec-uuid-1234-5678',
      status: 'COMPLETED',
      started_at: '2026-03-01T10:00:00Z',
      duration_seconds: 120,
      total_tasks: 5,
      completed_tasks: 5,
    },
    {
      id: 'exec-uuid-abcd-efgh',
      status: 'FAILED',
      started_at: '2026-03-01T11:00:00Z',
      duration_seconds: 45,
      total_tasks: 3,
      completed_tasks: 1,
    },
  ],
};

const MOCK_STATISTICS = {
  total_workflows: 25,
  completed_workflows: 24,
  failed_workflows: 1,
  running_workflows: 0,
  success_rate: 96.0,
  avg_execution_time_seconds: 135,
  avg_tasks_per_workflow: 4.8,
};

const MOCK_PERFORMANCE = {
  execution_time_trend: [],
  success_rate_trend: [],
  task_throughput: 10,
};

function setupHappyPath() {
  mockGetWorkflowHistory.mockResolvedValue(MOCK_WORKFLOW_HISTORY);
  mockGetWorkflowStatistics.mockResolvedValue(MOCK_STATISTICS);
  mockGetPerformanceMetrics.mockResolvedValue(MOCK_PERFORMANCE);
}

describe('WorkflowBuilder — loading and error', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading spinner initially', () => {
    mockGetWorkflowHistory.mockImplementation(() => new Promise(() => {}));
    mockGetWorkflowStatistics.mockImplementation(() => new Promise(() => {}));
    mockGetPerformanceMetrics.mockImplementation(() => new Promise(() => {}));

    render(<WorkflowBuilder />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows error alert when API fails', async () => {
    mockGetWorkflowHistory.mockRejectedValue(new Error('Server error'));
    mockGetWorkflowStatistics.mockResolvedValue({});
    mockGetPerformanceMetrics.mockResolvedValue({});

    render(<WorkflowBuilder />);

    await waitFor(() => {
      expect(
        screen.getByText(/Failed to load workflow data/i)
      ).toBeInTheDocument();
    });
  });
});

describe('WorkflowBuilder — data loaded', () => {
  // Helper to wait for loading to complete
  async function waitForLoad() {
    await waitFor(() => {
      expect(
        screen.getByText('Workflow Builder & Monitor')
      ).toBeInTheDocument();
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    setupHappyPath();
  });

  it('renders Workflow Builder & Monitor heading', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();
    expect(screen.getByText('Workflow Builder & Monitor')).toBeInTheDocument();
  });

  it('renders Workflow History, Statistics, Performance tabs', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();

    expect(
      screen.getByRole('tab', { name: /Workflow History/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /Statistics/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /Performance/i })
    ).toBeInTheDocument();
  });

  it('shows workflow executions count', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();

    expect(screen.getByText(/Workflow Executions \(2\)/i)).toBeInTheDocument();
  });

  it('shows workflow ID (truncated) in history table', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();

    // Component truncates to .substring(0, 8) + '...'
    expect(document.body.textContent).toContain('exec-uui');
  });

  it('shows status chips (COMPLETED, FAILED)', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();

    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
    expect(screen.getByText('FAILED')).toBeInTheDocument();
  });

  it('renders Refresh button', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();

    expect(
      screen.getByRole('button', { name: /Refresh/i })
    ).toBeInTheDocument();
  });

  it('clicking Refresh calls loadWorkflowData again', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();

    const initialCount = mockGetWorkflowHistory.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /Refresh/i }));

    await waitFor(() => {
      expect(mockGetWorkflowHistory.mock.calls.length).toBeGreaterThan(
        initialCount
      );
    });
  });

  it('Statistics tab shows total executions', async () => {
    render(<WorkflowBuilder />);
    await waitForLoad();

    fireEvent.click(screen.getByRole('tab', { name: /Statistics/i }));

    await waitFor(() => {
      expect(document.body.textContent).toContain('25');
    });
  });
});

describe('WorkflowBuilder — empty state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetWorkflowHistory.mockResolvedValue({ executions: [] });
    mockGetWorkflowStatistics.mockResolvedValue({});
    mockGetPerformanceMetrics.mockResolvedValue({});
  });

  it('shows empty state when no executions', async () => {
    render(<WorkflowBuilder />);
    await waitFor(() => {
      expect(
        screen.getByText(/No workflow executions found/i)
      ).toBeInTheDocument();
    });
  });
});
