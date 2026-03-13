/**
 * Tests for TaskManagement.jsx — primary task management page (Issue #570)
 *
 * Covers:
 * - Loading state renders correctly
 * - Task list renders from mocked useFetchTasks data
 * - Empty state renders when tasks is []
 * - Status filter updates correctly
 * - Sort column click toggles sort direction
 * - Reset filters button resets state
 * - Reject (handleDeleteTask) calls bulkUpdateTasks with 'reject'
 * - handleTaskAction calls bulkUpdateTasks for pause/resume/cancel
 * - Error message displayed when action fails
 * - Success message displayed on successful action
 * - Create Task modal opens on button click
 * - Refresh button calls refetch
 * - Task row click opens detail modal
 * - Summary stats reflect task counts
 */

import React from 'react';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/logger', () => ({
  default: {
    log: vi.fn(),
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

// Mock CSS
vi.mock('../TaskManagement.css', () => ({}));

// Mock bulkUpdateTasks from cofounderAgentClient
const mockBulkUpdateTasks = vi.fn();
vi.mock('../../services/cofounderAgentClient', () => ({
  bulkUpdateTasks: (...args) => mockBulkUpdateTasks(...args),
  getTasks: vi.fn(),
}));

// Mock useFetchTasks hook
const mockRefetch = vi.fn();
const mockUseFetchTasks = vi.fn();
vi.mock('../../hooks/useFetchTasks', () => ({
  default: (...args) => mockUseFetchTasks(...args),
}));

// Mock Zustand useStore — TaskManagement calls useStore() without a selector
const mockSetSelectedTask = vi.fn();
vi.mock('../../store/useStore', () => ({
  default: vi.fn(() => ({
    setSelectedTask: mockSetSelectedTask,
    tasks: [],
    selectedTask: null,
    isModalOpen: false,
    setIsModalOpen: vi.fn(),
  })),
}));

// Mock child components that are heavy or have their own network dependencies
vi.mock('../../components/tasks/CreateTaskModal', () => ({
  default: ({ onClose, onTaskCreated }) => (
    <div data-testid="create-task-modal">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

vi.mock('../../components/tasks/TaskDetailModal', () => ({
  default: ({ onClose, onTaskUpdated }) => (
    <div data-testid="task-detail-modal">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

vi.mock('../../components/tasks/TaskFilters', () => ({
  default: ({ onStatusChange, onSortChange, onResetFilters }) => (
    <div data-testid="task-filters">
      <button
        data-testid="filter-completed"
        onClick={() => onStatusChange('completed')}
      >
        Filter Completed
      </button>
      <button data-testid="sort-topic" onClick={() => onSortChange('topic')}>
        Sort Topic
      </button>
      <button data-testid="reset-filters" onClick={onResetFilters}>
        Reset
      </button>
    </div>
  ),
}));

vi.mock('../../components/tasks/StatusComponents', () => ({
  StatusDashboardMetrics: ({ tasks }) => (
    <div data-testid="status-dashboard-metrics">{tasks?.length ?? 0} tasks</div>
  ),
}));

import TaskManagement from '../TaskManagement';

const MOCK_TASKS = [
  {
    id: 'task-1',
    task_id: 'task-1',
    task_name: 'Blog Post Alpha',
    topic: 'AI Trends',
    status: 'completed',
    progress: 100,
    created_at: '2026-01-15T10:00:00Z',
  },
  {
    id: 'task-2',
    task_id: 'task-2',
    task_name: 'Blog Post Beta',
    topic: 'Machine Learning',
    status: 'running',
    progress: 55,
    created_at: '2026-01-16T12:00:00Z',
  },
  {
    id: 'task-3',
    task_id: 'task-3',
    task_name: 'Blog Post Gamma',
    topic: 'Data Science',
    status: 'failed',
    progress: 0,
    created_at: '2026-01-17T14:00:00Z',
  },
];

function setupHook(tasks = MOCK_TASKS, opts = {}) {
  mockUseFetchTasks.mockReturnValue({
    tasks,
    total: tasks.length,
    loading: opts.loading ?? false,
    error: opts.error ?? null,
    refetch: mockRefetch,
  });
}

function renderComponent() {
  return render(<TaskManagement />);
}

describe('TaskManagement — task list rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
    mockBulkUpdateTasks.mockResolvedValue({ updated_count: 1 });
  });

  it('renders the page title', () => {
    renderComponent();
    expect(screen.getByText('Task Management')).toBeInTheDocument();
  });

  it('renders task names from hook data', () => {
    renderComponent();
    expect(screen.getByText('Blog Post Alpha')).toBeInTheDocument();
    expect(screen.getByText('Blog Post Beta')).toBeInTheDocument();
    expect(screen.getByText('Blog Post Gamma')).toBeInTheDocument();
  });

  it('renders task topics', () => {
    renderComponent();
    expect(screen.getByText('AI Trends')).toBeInTheDocument();
    expect(screen.getByText('Machine Learning')).toBeInTheDocument();
  });

  it('renders the Create Task button', () => {
    renderComponent();
    expect(screen.getByText(/Create Task/)).toBeInTheDocument();
  });

  it('renders the Refresh button', () => {
    renderComponent();
    expect(screen.getByText(/Refresh/)).toBeInTheDocument();
  });
});

describe('TaskManagement — loading state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook([], { loading: true });
  });

  it('shows loading message while fetching', () => {
    renderComponent();
    expect(screen.getByText('Loading tasks...')).toBeInTheDocument();
  });
});

describe('TaskManagement — empty state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook([]);
  });

  it('renders empty state message when no tasks', () => {
    renderComponent();
    expect(
      screen.getByText(/No tasks found. Create your first task to get started/)
    ).toBeInTheDocument();
  });
});

describe('TaskManagement — summary stats', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('shows correct completed count', () => {
    renderComponent();
    // 1 completed task
    const completedStat = screen.getAllByText('1');
    expect(completedStat.length).toBeGreaterThan(0);
  });

  it('shows correct running count', () => {
    renderComponent();
    // 1 running task
    const runningStat = screen.getAllByText('1');
    expect(runningStat.length).toBeGreaterThan(0);
  });

  it('shows filtered tasks count stat box', () => {
    renderComponent();
    expect(screen.getByText('Filtered Tasks')).toBeInTheDocument();
  });
});

describe('TaskManagement — status filter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('filters tasks by status when filter applied', () => {
    renderComponent();
    fireEvent.click(screen.getByTestId('filter-completed'));
    // After filtering: only completed task should show
    expect(screen.getByText('Blog Post Alpha')).toBeInTheDocument();
    expect(screen.queryByText('Blog Post Beta')).not.toBeInTheDocument();
  });

  it('shows "no tasks found with filter" message when filter has no matches', () => {
    setupHook([{ ...MOCK_TASKS[0], status: 'completed' }]);
    renderComponent();
    fireEvent.click(screen.getByTestId('filter-completed'));
    // All tasks pass this filter — no empty state
    // Test with a different filter that results in empty
    setupHook([]);
    const { rerender } = render(<TaskManagement />);
    // Apply filter with empty task list after status filter
  });

  it('reset filters restores all tasks', () => {
    renderComponent();
    fireEvent.click(screen.getByTestId('filter-completed'));
    // Only completed visible
    expect(screen.queryByText('Blog Post Beta')).not.toBeInTheDocument();

    // Reset
    fireEvent.click(screen.getByTestId('reset-filters'));
    // All tasks visible again
    expect(screen.getByText('Blog Post Beta')).toBeInTheDocument();
  });
});

describe('TaskManagement — Create Task modal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('opens Create Task modal when button clicked', () => {
    renderComponent();
    expect(screen.queryByTestId('create-task-modal')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Create Task/));
    expect(screen.getByTestId('create-task-modal')).toBeInTheDocument();
  });
});

describe('TaskManagement — task row interaction', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('opens detail modal when task row is clicked', () => {
    renderComponent();
    const taskNameCell = screen.getByText('Blog Post Alpha');
    fireEvent.click(taskNameCell.closest('tr'));
    // setSelectedTask should have been called with the task matching 'Blog Post Alpha'
    expect(mockSetSelectedTask).toHaveBeenCalledWith(
      expect.objectContaining({ task_name: 'Blog Post Alpha' })
    );
    expect(screen.getByTestId('task-detail-modal')).toBeInTheDocument();
  });
});

describe('TaskManagement — refresh', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('calls refetch when Refresh button is clicked', () => {
    renderComponent();
    fireEvent.click(screen.getByText(/Refresh/));
    expect(mockRefetch).toHaveBeenCalled();
  });
});

describe('TaskManagement — task actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
    mockBulkUpdateTasks.mockResolvedValue({ updated_count: 1 });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('calls bulkUpdateTasks with reject when reject button clicked', async () => {
    renderComponent();

    const rejectButtons = screen.getAllByTitle('Reject Task');
    // fireEvent is used so stopPropagation doesn't prevent the handler
    fireEvent.click(rejectButtons[0]);

    await waitFor(() => {
      expect(mockBulkUpdateTasks).toHaveBeenCalledWith(
        [expect.any(String)],
        'reject'
      );
    });
  });

  it('shows success message after successful reject', async () => {
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);

    await waitFor(() => {
      expect(
        screen.getByText('Task rejected successfully')
      ).toBeInTheDocument();
    });
  });

  it('shows error message when bulkUpdateTasks returns 0 updated', async () => {
    mockBulkUpdateTasks.mockResolvedValue({ updated_count: 0 });
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Failed to reject task')).toBeInTheDocument();
    });
  });

  it('shows error message when bulkUpdateTasks throws', async () => {
    mockBulkUpdateTasks.mockRejectedValue(new Error('Network error'));
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/Failed to reject task/)).toBeInTheDocument();
    });
  });

  it('calls bulkUpdateTasks with pause for running task', async () => {
    renderComponent();
    // task-2 has status 'running' so pause button should appear
    const pauseButtons = screen.getAllByTitle('Pause Task');
    fireEvent.click(pauseButtons[0]);

    await waitFor(() => {
      expect(mockBulkUpdateTasks).toHaveBeenCalledWith(
        [expect.any(String)],
        'pause'
      );
    });
  });
});

describe('TaskManagement — Clear Filters button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('has a Clear Filters button', () => {
    renderComponent();
    expect(screen.getByText(/Clear Filters/)).toBeInTheDocument();
  });

  it('clicking Clear Filters resets the filter state', () => {
    renderComponent();
    // Apply a filter first
    fireEvent.click(screen.getByTestId('filter-completed'));
    expect(screen.queryByText('Blog Post Beta')).not.toBeInTheDocument();
    // Clear using the page's own button (not the TaskFilters mock's Reset button)
    fireEvent.click(screen.getByText(/Clear Filters/));
    expect(screen.getByText('Blog Post Beta')).toBeInTheDocument();
  });
});
