/**
 * Component Tests for CostMetricsDashboard
 *
 * Tests cover:
 * 1. Loading state rendering
 * 2. Successful data fetch and display
 * 3. Error state handling
 * 4. Time range selector changes
 * 5. Budget status display
 * 6. Cost breakdown by phase and model
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import CostMetricsDashboard from '../CostMetricsDashboard';

// Mock the cofounderAgentClient service
vi.mock('../../services/cofounderAgentClient', () => ({
  getCostMetrics: vi.fn(),
  getCostsByPhase: vi.fn(),
  getCostsByModel: vi.fn(),
  getCostHistory: vi.fn(),
  getBudgetStatus: vi.fn(),
}));

// Mock the validation schemas
vi.mock('../../services/responseValidationSchemas', () => ({
  validateCostMetrics: vi.fn((data) => data),
  validateCostsByPhase: vi.fn((data) => data),
  validateCostsByModel: vi.fn((data) => data),
  validateCostHistory: vi.fn((data) => data),
  validateBudgetStatus: vi.fn((data) => data),
  safeValidate: vi.fn((validator, data) => data),
}));

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

// Mock CSS import
vi.mock('../CostMetricsDashboard.css', () => ({}));

import {
  getCostMetrics,
  getCostsByPhase,
  getCostsByModel,
  getCostHistory,
  getBudgetStatus,
} from '../../services/cofounderAgentClient';

const mockMetrics = {
  total_cost: 42.75,
  avg_cost_per_task: 0.000213,
  total_tasks: 200750,
};

const mockBudgetStatus = {
  amount_spent: 42.75,
  monthly_budget: 150.0,
  percent_used: 28.5,
  amount_remaining: 107.25,
};

const mockPhaseData = {
  phases: {
    research: 0.00015,
    draft: 0.00025,
    qa: 0.00008,
  },
};

const mockModelData = {
  models: {
    claude: 0.00015,
    gpt4: 0.0001,
  },
};

const mockHistoryData = {
  daily_data: [
    { date: '2026-01-01', cost: 1.5 },
    { date: '2026-02-01', cost: 2.1 },
    { date: '2026-03-01', cost: 1.8 },
  ],
};

function setupMocksSuccess() {
  getCostMetrics.mockResolvedValue(mockMetrics);
  getCostsByPhase.mockResolvedValue(mockPhaseData);
  getCostsByModel.mockResolvedValue(mockModelData);
  getCostHistory.mockResolvedValue(mockHistoryData);
  getBudgetStatus.mockResolvedValue(mockBudgetStatus);
}

// ============================================================================
// LOADING STATE
// ============================================================================

describe('CostMetricsDashboard — Loading State', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading indicator while fetching data', async () => {
    // Delay the API response so loading state is visible
    getCostMetrics.mockReturnValue(new Promise(() => {}));
    getCostsByPhase.mockReturnValue(new Promise(() => {}));
    getCostsByModel.mockReturnValue(new Promise(() => {}));
    getCostHistory.mockReturnValue(new Promise(() => {}));
    getBudgetStatus.mockReturnValue(new Promise(() => {}));

    render(<CostMetricsDashboard />);

    expect(screen.getByText(/loading cost metrics/i)).toBeInTheDocument();
  });

  it('renders the dashboard title always', () => {
    getCostMetrics.mockReturnValue(new Promise(() => {}));
    getCostsByPhase.mockReturnValue(new Promise(() => {}));
    getCostsByModel.mockReturnValue(new Promise(() => {}));
    getCostHistory.mockReturnValue(new Promise(() => {}));
    getBudgetStatus.mockReturnValue(new Promise(() => {}));

    render(<CostMetricsDashboard />);

    expect(screen.getByText(/cost metrics dashboard/i)).toBeInTheDocument();
  });
});

// ============================================================================
// SUCCESSFUL DATA DISPLAY
// ============================================================================

describe('CostMetricsDashboard — Success State', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocksSuccess();
  });

  it('renders metric cards after successful data load', async () => {
    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Total Cost (Period)')).toBeInTheDocument();
    });

    expect(screen.getByText('Avg Cost/Task')).toBeInTheDocument();
    expect(screen.getByText('Total Tasks')).toBeInTheDocument();
    expect(screen.getByText('Monthly Budget')).toBeInTheDocument();
  });

  it('formats total cost as currency', async () => {
    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(screen.getByText('$42.75')).toBeInTheDocument();
    });
  });

  it('displays budget overview section when budget data is available', async () => {
    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/monthly budget overview/i)).toBeInTheDocument();
    });

    // Check remaining budget is shown
    expect(screen.getByText(/107.25/)).toBeInTheDocument();
  });

  it('displays cost by AI model section when model data is available', async () => {
    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/cost by ai model/i)).toBeInTheDocument();
    });

    expect(screen.getByText('CLAUDE')).toBeInTheDocument();
    expect(screen.getByText('GPT4')).toBeInTheDocument();
  });
});

// ============================================================================
// ERROR STATE
// ============================================================================

describe('CostMetricsDashboard — Error State', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('displays error message when API call fails', async () => {
    getCostMetrics.mockRejectedValue(new Error('Network error'));
    getCostsByPhase.mockRejectedValue(new Error('Network error'));
    getCostsByModel.mockRejectedValue(new Error('Network error'));
    getCostHistory.mockRejectedValue(new Error('Network error'));
    getBudgetStatus.mockRejectedValue(new Error('Network error'));

    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/network error/i)).toBeInTheDocument();
    });

    expect(
      screen.getByText(/check your database connection/i)
    ).toBeInTheDocument();
  });

  it('hides loading indicator after error', async () => {
    getCostMetrics.mockRejectedValue(new Error('Server error'));
    getCostsByPhase.mockRejectedValue(new Error('Server error'));
    getCostsByModel.mockRejectedValue(new Error('Server error'));
    getCostHistory.mockRejectedValue(new Error('Server error'));
    getBudgetStatus.mockRejectedValue(new Error('Server error'));

    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(
        screen.queryByText(/loading cost metrics/i)
      ).not.toBeInTheDocument();
    });
  });

  it('shows error when metrics validation fails', async () => {
    const { safeValidate } =
      await import('../../services/responseValidationSchemas');
    // Make safeValidate return null for metrics to simulate validation failure
    safeValidate.mockImplementationOnce(() => null); // metrics fails

    getCostMetrics.mockResolvedValue(mockMetrics);
    getCostsByPhase.mockResolvedValue(mockPhaseData);
    getCostsByModel.mockResolvedValue(mockModelData);
    getCostHistory.mockResolvedValue(mockHistoryData);
    getBudgetStatus.mockResolvedValue(mockBudgetStatus);

    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(
        screen.getByText(/invalid api response format/i)
      ).toBeInTheDocument();
    });
  });
});

// ============================================================================
// TIME RANGE SELECTOR
// ============================================================================

describe('CostMetricsDashboard — Time Range Selector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocksSuccess();
  });

  it('renders time range dropdown with default month selection', async () => {
    render(<CostMetricsDashboard />);

    const select = screen.getByRole('combobox');
    expect(select).toBeInTheDocument();
    expect(select.value).toBe('month');
  });

  it('re-fetches data when time range changes', async () => {
    render(<CostMetricsDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Total Cost (Period)')).toBeInTheDocument();
    });

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'week' } });

    await waitFor(() => {
      // getCostsByPhase should be called twice — initial + after range change
      expect(getCostsByPhase).toHaveBeenCalledWith('week');
    });
  });

  it('renders all time range options', () => {
    getCostMetrics.mockReturnValue(new Promise(() => {}));
    getCostsByPhase.mockReturnValue(new Promise(() => {}));
    getCostsByModel.mockReturnValue(new Promise(() => {}));
    getCostHistory.mockReturnValue(new Promise(() => {}));
    getBudgetStatus.mockReturnValue(new Promise(() => {}));

    render(<CostMetricsDashboard />);

    const options = screen.getAllByRole('option');
    const optionValues = options.map((o) => o.value);
    expect(optionValues).toContain('today');
    expect(optionValues).toContain('week');
    expect(optionValues).toContain('month');
  });
});
