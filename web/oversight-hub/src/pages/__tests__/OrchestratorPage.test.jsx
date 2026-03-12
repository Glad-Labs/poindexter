/**
 * Component Tests for OrchestratorPage
 *
 * Tests cover:
 * 1. Initial render and layout
 * 2. Loading orchestrations on mount
 * 3. Error state display
 * 4. Submit request form
 * 5. Approval workflow toggle
 * 6. Execution stats display
 * 7. Empty orchestrations state
 * 8. Orchestration list rendering
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import OrchestratorPage from '../OrchestratorPage';

// Mock the cofounderAgentClient service
vi.mock('../../services/cofounderAgentClient', () => ({
  makeRequest: vi.fn(),
}));

// Mock the unifiedStatusService
vi.mock('../../services/unifiedStatusService', () => ({
  unifiedStatusService: {
    approve: vi.fn(),
    reject: vi.fn(),
  },
}));

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

import { makeRequest } from '../../services/cofounderAgentClient';
import { unifiedStatusService } from '../../services/unifiedStatusService';

const mockExecutions = [
  {
    id: 'exec-001',
    status: 'completed',
    user_request: 'Write a blog post about AI',
    result: { content: 'Blog post content...' },
    created_at: '2026-03-12T10:00:00Z',
    completed_at: '2026-03-12T10:05:00Z',
  },
  {
    id: 'exec-002',
    status: 'pending_approval',
    user_request: 'Post to Twitter',
    created_at: '2026-03-12T11:00:00Z',
  },
];

const mockStats = {
  total_executions: 42,
  success_rate: 95,
  avg_execution_time: 3.5,
  patterns_learned: 12,
};

function setupMocksSuccess(executions = mockExecutions, stats = mockStats) {
  makeRequest.mockImplementation((url) => {
    if (url.includes('/executions?')) {
      return Promise.resolve({ executions });
    }
    if (url.includes('/stats')) {
      return Promise.resolve({ stats });
    }
    if (url.includes('/process')) {
      return Promise.resolve({ execution_id: 'exec-new-001' });
    }
    return Promise.resolve({});
  });
}

// ============================================================================
// INITIAL RENDER
// ============================================================================

describe('OrchestratorPage — Initial Render', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllTimers();
  });

  it('renders the page title', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    expect(screen.getByText(/orchestrator dashboard/i)).toBeInTheDocument();
  });

  it('renders the Submit Request form', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    expect(
      screen.getByText(/submit request for orchestration/i)
    ).toBeInTheDocument();

    expect(
      screen.getByPlaceholderText(/describe what you want the orchestrator/i)
    ).toBeInTheDocument();
  });

  it('renders Submit to Orchestrator button disabled when textarea is empty', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    const submitBtn = screen.getByRole('button', {
      name: /submit to orchestrator/i,
    });
    expect(submitBtn).toBeDisabled();
  });

  it('renders Approval Workflow toggle section', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    expect(screen.getByText('Approval Workflow')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /disabled/i })
    ).toBeInTheDocument();
  });

  it('renders Execution History section', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    expect(screen.getByText('Execution History')).toBeInTheDocument();
  });
});

// ============================================================================
// DATA LOADING
// ============================================================================

describe('OrchestratorPage — Data Loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads orchestrations on mount', async () => {
    setupMocksSuccess();
    render(<OrchestratorPage />);

    await waitFor(() => {
      expect(makeRequest).toHaveBeenCalledWith(
        '/api/orchestrator/executions?limit=50',
        'GET'
      );
    });
  });

  it('loads execution stats on mount', async () => {
    setupMocksSuccess();
    render(<OrchestratorPage />);

    await waitFor(() => {
      expect(makeRequest).toHaveBeenCalledWith(
        '/api/orchestrator/stats',
        'GET'
      );
    });
  });

  it('displays stats when loaded', async () => {
    setupMocksSuccess();
    render(<OrchestratorPage />);

    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument();
    });

    expect(screen.getByText('Total Executions')).toBeInTheDocument();
    expect(screen.getByText('95%')).toBeInTheDocument();
    expect(screen.getByText('Success Rate')).toBeInTheDocument();
  });

  it('shows empty state when no orchestrations exist', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    await waitFor(() => {
      expect(screen.getByText('No orchestrations yet')).toBeInTheDocument();
    });
  });

  it('renders orchestration items when data is loaded', async () => {
    setupMocksSuccess();
    render(<OrchestratorPage />);

    await waitFor(() => {
      expect(
        screen.getByText('Write a blog post about AI')
      ).toBeInTheDocument();
    });

    expect(screen.getByText('Post to Twitter')).toBeInTheDocument();
  });
});

// ============================================================================
// ERROR STATE
// ============================================================================

describe('OrchestratorPage — Error State', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('displays error when orchestrations fetch fails', async () => {
    makeRequest.mockImplementation((url) => {
      if (url.includes('/executions')) {
        return Promise.reject(new Error('Connection refused'));
      }
      return Promise.resolve({ stats: mockStats });
    });

    render(<OrchestratorPage />);

    await waitFor(() => {
      expect(screen.getByText(/connection refused/i)).toBeInTheDocument();
    });
  });
});

// ============================================================================
// SUBMIT REQUEST FORM
// ============================================================================

describe('OrchestratorPage — Submit Request', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('enables submit button when textarea has content', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want the orchestrator/i
    );
    await userEvent.type(textarea, 'Write a Twitter thread about AI');

    const submitBtn = screen.getByRole('button', {
      name: /submit to orchestrator/i,
    });
    expect(submitBtn).not.toBeDisabled();
  });

  it('submits request and reloads orchestrations on success', async () => {
    // Mock window.alert
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    setupMocksSuccess([]);

    render(<OrchestratorPage />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want the orchestrator/i
    );
    await userEvent.type(textarea, 'Write a Twitter thread about AI');

    const submitBtn = screen.getByRole('button', {
      name: /submit to orchestrator/i,
    });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(makeRequest).toHaveBeenCalledWith(
        '/api/orchestrator/process',
        'POST',
        { user_request: 'Write a Twitter thread about AI' }
      );
    });

    expect(alertSpy).toHaveBeenCalledWith(
      expect.stringContaining('Request submitted')
    );

    alertSpy.mockRestore();
  });

  it('clears textarea after successful submission', async () => {
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    setupMocksSuccess([]);

    render(<OrchestratorPage />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want the orchestrator/i
    );
    await userEvent.type(textarea, 'Write a Twitter thread about AI');
    fireEvent.click(
      screen.getByRole('button', { name: /submit to orchestrator/i })
    );

    await waitFor(() => {
      expect(textarea.value).toBe('');
    });

    vi.restoreAllMocks();
  });
});

// ============================================================================
// APPROVAL WORKFLOW TOGGLE
// ============================================================================

describe('OrchestratorPage — Approval Workflow Toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('toggles approval mode on button click', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    const toggleBtn = screen.getByRole('button', { name: /disabled/i });
    expect(toggleBtn).toBeInTheDocument();

    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /enabled/i })
      ).toBeInTheDocument();
    });
  });

  it('can toggle approval mode off again', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    // Turn on
    fireEvent.click(screen.getByRole('button', { name: /disabled/i }));
    await waitFor(() => {
      screen.getByRole('button', { name: /enabled/i });
    });

    // Turn off
    fireEvent.click(screen.getByRole('button', { name: /enabled/i }));
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /disabled/i })
      ).toBeInTheDocument();
    });
  });
});

// ============================================================================
// REFRESH BUTTON
// ============================================================================

describe('OrchestratorPage — Refresh Button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('re-fetches orchestrations when Refresh is clicked', async () => {
    setupMocksSuccess([]);
    render(<OrchestratorPage />);

    await waitFor(() => {
      expect(makeRequest).toHaveBeenCalledWith(
        '/api/orchestrator/executions?limit=50',
        'GET'
      );
    });

    const callCountBefore = makeRequest.mock.calls.filter((c) =>
      c[0].includes('/executions')
    ).length;

    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    await waitFor(() => {
      const callCountAfter = makeRequest.mock.calls.filter((c) =>
        c[0].includes('/executions')
      ).length;
      expect(callCountAfter).toBeGreaterThan(callCountBefore);
    });
  });
});
