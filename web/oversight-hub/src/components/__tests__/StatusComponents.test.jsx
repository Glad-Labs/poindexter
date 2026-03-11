import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import {
  StatusAuditTrail,
  StatusTimeline,
  ValidationFailureUI,
  StatusDashboardMetrics,
} from '../tasks/StatusComponents';

// Mock unifiedStatusService
vi.mock('../../services/unifiedStatusService', () => ({
  unifiedStatusService: {
    getHistory: vi.fn(),
    getFailures: vi.fn(),
    getMetrics: vi.fn(),
  },
}));

// Mock Constants/statusEnums
vi.mock('../../Constants/statusEnums', () => ({
  STATUS_COLORS: {
    pending: '#gray',
    in_progress: '#blue',
    completed: '#green',
    failed: '#red',
  },
  getStatusLabel: vi.fn((status) => {
    const labels = {
      pending: 'Pending',
      in_progress: 'In Progress',
      completed: 'Completed',
      failed: 'Failed',
    };
    return labels[status] || status;
  }),
}));

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: {
    debug: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

import { unifiedStatusService } from '../../services/unifiedStatusService';

describe('StatusAuditTrail Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading spinner while fetching', () => {
    unifiedStatusService.getHistory.mockReturnValue(new Promise(() => {})); // never resolves
    render(<StatusAuditTrail taskId="task-1" />);
    expect(document.querySelector('.MuiCircularProgress-root')).toBeInTheDocument();
  });

  it('shows empty message when history is empty array', async () => {
    unifiedStatusService.getHistory.mockResolvedValue([]);
    render(<StatusAuditTrail taskId="task-1" />);
    await waitFor(() => {
      expect(screen.getByText(/No status changes recorded yet/)).toBeInTheDocument();
    });
  });

  it('shows empty message when history is an empty object', async () => {
    unifiedStatusService.getHistory.mockResolvedValue({});
    render(<StatusAuditTrail taskId="task-1" />);
    await waitFor(() => {
      expect(screen.getByText(/No status changes recorded yet/)).toBeInTheDocument();
    });
  });

  it('displays history entries from array response', async () => {
    unifiedStatusService.getHistory.mockResolvedValue([
      {
        old_status: 'pending',
        new_status: 'in_progress',
        timestamp: new Date('2026-01-01T10:00:00Z').toISOString(),
        reason: 'Started processing',
      },
    ]);
    render(<StatusAuditTrail taskId="task-1" />);
    await waitFor(() => {
      expect(screen.getByText(/pending → in_progress/)).toBeInTheDocument();
      expect(screen.getByText(/Started processing/)).toBeInTheDocument();
    });
  });

  it('displays history from data.history nested array', async () => {
    unifiedStatusService.getHistory.mockResolvedValue({
      history: [
        {
          old_status: 'in_progress',
          new_status: 'completed',
          timestamp: new Date('2026-01-01T12:00:00Z').toISOString(),
        },
      ],
    });
    render(<StatusAuditTrail taskId="task-1" />);
    await waitFor(() => {
      expect(screen.getByText(/in_progress → completed/)).toBeInTheDocument();
    });
  });

  it('shows error message when fetch fails', async () => {
    unifiedStatusService.getHistory.mockRejectedValue(new Error('Network error'));
    render(<StatusAuditTrail taskId="task-1" />);
    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument();
    });
  });

  it('does not fetch when taskId is not provided', () => {
    render(<StatusAuditTrail taskId={null} />);
    expect(unifiedStatusService.getHistory).not.toHaveBeenCalled();
  });

  it('renders metadata when present in history entry', async () => {
    unifiedStatusService.getHistory.mockResolvedValue([
      {
        old_status: 'pending',
        new_status: 'in_progress',
        timestamp: new Date().toISOString(),
        metadata: { agent: 'content_agent' },
      },
    ]);
    render(<StatusAuditTrail taskId="task-1" />);
    await waitFor(() => {
      expect(screen.getByText(/Metadata:/)).toBeInTheDocument();
    });
  });
});

describe('StatusTimeline Component', () => {
  it('shows current status when no history is provided', () => {
    render(<StatusTimeline currentStatus="in_progress" statusHistory={[]} />);
    expect(screen.getByText(/in_progress/)).toBeInTheDocument();
  });

  it('shows "Status Progression" heading when history is provided', () => {
    const history = [
      { new_status: 'pending', timestamp: new Date().toISOString() },
      { new_status: 'in_progress', timestamp: new Date().toISOString() },
    ];
    render(<StatusTimeline currentStatus="in_progress" statusHistory={history} />);
    expect(screen.getByText('Status Progression')).toBeInTheDocument();
  });

  it('renders status entries in non-compact mode', () => {
    const history = [
      { new_status: 'pending', timestamp: new Date().toISOString() },
      { new_status: 'in_progress', timestamp: new Date().toISOString() },
    ];
    render(
      <StatusTimeline
        currentStatus="in_progress"
        statusHistory={history}
        compact={false}
      />
    );
    expect(screen.getByText('pending')).toBeInTheDocument();
    expect(screen.getByText('in_progress')).toBeInTheDocument();
  });

  it('renders Chips in compact mode', () => {
    const history = [
      { new_status: 'pending', timestamp: new Date().toISOString() },
      { new_status: 'completed', timestamp: new Date().toISOString() },
    ];
    render(
      <StatusTimeline
        currentStatus="completed"
        statusHistory={history}
        compact={true}
      />
    );
    // Compact mode uses Chips — getStatusLabel is mocked to return 'Pending', 'Completed'
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });

  it('defaults to non-compact mode', () => {
    const history = [
      { new_status: 'pending', timestamp: new Date().toISOString() },
    ];
    render(<StatusTimeline currentStatus="pending" statusHistory={history} />);
    // In non-compact mode the raw status text is displayed
    expect(screen.getByText('pending')).toBeInTheDocument();
  });
});

describe('ValidationFailureUI Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows all gates passed when task has no validation failures', () => {
    const task = {
      task_metadata: {
        validation_details: {
          base_content_valid: true,
          length_gate_passes: true,
          style_gate_passes: true,
          seo_gate_passes: true,
        },
      },
    };
    render(<ValidationFailureUI task={task} />);
    expect(screen.getByText(/All validation gates passed/)).toBeInTheDocument();
  });

  it('shows content validity failure when base_content_valid is false', () => {
    const task = {
      task_metadata: {
        validation_details: {
          base_content_valid: false,
          length_gate_passes: true,
          style_gate_passes: true,
          seo_gate_passes: true,
        },
      },
    };
    render(<ValidationFailureUI task={task} />);
    expect(screen.getByText(/Content Validity/)).toBeInTheDocument();
    expect(screen.getByText(/Validation Gate/)).toBeInTheDocument();
  });

  it('shows length gate failure with details', () => {
    const task = {
      task_metadata: {
        validation_details: {
          base_content_valid: true,
          length_gate_passes: false,
          length_gate_detail: {
            word_count: 300,
            target: 800,
            minimum: 600,
            tolerance_percent: 20,
          },
          style_gate_passes: true,
          seo_gate_passes: true,
        },
      },
    };
    render(<ValidationFailureUI task={task} />);
    expect(screen.getByText(/Length Gate/)).toBeInTheDocument();
    expect(screen.getByText(/Word count insufficient/)).toBeInTheDocument();
  });

  it('shows SEO gate failure', () => {
    const task = {
      task_metadata: {
        validation_details: {
          base_content_valid: true,
          length_gate_passes: true,
          style_gate_passes: true,
          seo_gate_passes: false,
          seo_gate_detail: 'Missing meta description',
        },
      },
    };
    render(<ValidationFailureUI task={task} />);
    expect(screen.getByText(/SEO Gate/)).toBeInTheDocument();
  });

  it('fetches failures from service when no task metadata (fallback)', async () => {
    unifiedStatusService.getFailures.mockResolvedValue([]);
    render(<ValidationFailureUI taskId="task-1" />);
    await waitFor(() => {
      expect(unifiedStatusService.getFailures).toHaveBeenCalledWith('task-1', 50);
    });
  });

  it('shows loading spinner during fetch', () => {
    unifiedStatusService.getFailures.mockReturnValue(new Promise(() => {}));
    render(<ValidationFailureUI taskId="task-1" />);
    expect(document.querySelector('.MuiCircularProgress-root')).toBeInTheDocument();
  });

  it('shows error when fetch fails', async () => {
    unifiedStatusService.getFailures.mockRejectedValue(
      new Error('Fetch failed')
    );
    render(<ValidationFailureUI taskId="task-1" />);
    await waitFor(() => {
      expect(screen.getByText(/Fetch failed/)).toBeInTheDocument();
    });
  });

  it('shows "Work Preserved" info when there are failures', () => {
    const task = {
      task_metadata: {
        validation_details: {
          base_content_valid: false,
          length_gate_passes: true,
          style_gate_passes: true,
          seo_gate_passes: true,
        },
        word_count: 450,
      },
    };
    render(<ValidationFailureUI task={task} />);
    expect(screen.getByText(/Work Preserved/)).toBeInTheDocument();
  });
});

describe('StatusDashboardMetrics Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading spinner while fetching', () => {
    unifiedStatusService.getMetrics.mockReturnValue(new Promise(() => {}));
    render(<StatusDashboardMetrics />);
    expect(document.querySelector('.MuiCircularProgress-root')).toBeInTheDocument();
  });

  it('shows error when fetch fails', async () => {
    unifiedStatusService.getMetrics.mockRejectedValue(new Error('Server error'));
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText(/Server error/)).toBeInTheDocument();
    });
  });

  it('shows "No metrics available" when metrics is null', async () => {
    unifiedStatusService.getMetrics.mockResolvedValue(null);
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText(/No metrics available/)).toBeInTheDocument();
    });
  });

  it('shows status distribution when metrics are available', async () => {
    unifiedStatusService.getMetrics.mockResolvedValue({
      status_distribution: {
        pending: 5,
        completed: 12,
      },
    });
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('Status Distribution')).toBeInTheDocument();
      expect(screen.getByText('pending')).toBeInTheDocument();
      expect(screen.getByText('5')).toBeInTheDocument();
      expect(screen.getByText('completed')).toBeInTheDocument();
      expect(screen.getByText('12')).toBeInTheDocument();
    });
  });

  it('shows average processing time when available', async () => {
    unifiedStatusService.getMetrics.mockResolvedValue({
      status_distribution: {},
      average_processing_time: 120,
    });
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('Average Processing Time')).toBeInTheDocument();
      // 120 / 60 = 2 seconds
      expect(screen.getByText(/2 seconds/)).toBeInTheDocument();
    });
  });

  it('shows success rate when available', async () => {
    unifiedStatusService.getMetrics.mockResolvedValue({
      status_distribution: {},
      success_rate: 0.875,
    });
    render(<StatusDashboardMetrics />);
    await waitFor(() => {
      expect(screen.getByText('Success Rate')).toBeInTheDocument();
      expect(screen.getByText(/87.5%/)).toBeInTheDocument();
    });
  });
});
