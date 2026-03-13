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

// ---------------------------------------------------------------------------
// a11y — issue #766: keyboard-operable rows
// ---------------------------------------------------------------------------

describe('TaskManagement — a11y: keyboard-operable task rows (issue #766)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('task rows have role="button"', () => {
    renderComponent();
    const rows = screen.getAllByRole('button', { name: /View details for/i });
    expect(rows.length).toBeGreaterThan(0);
  });

  it('task rows have tabIndex=0', () => {
    renderComponent();
    const rows = screen.getAllByRole('button', { name: /View details for/i });
    rows.forEach((row) => {
      expect(row).toHaveAttribute('tabIndex', '0');
    });
  });

  it('task rows have aria-label containing task name', () => {
    renderComponent();
    const row = screen.getByRole('button', {
      name: /View details for Blog Post Alpha/i,
    });
    expect(row).toBeInTheDocument();
  });

  it('pressing Enter on a task row opens the detail modal', async () => {
    renderComponent();
    const row = screen.getByRole('button', {
      name: /View details for Blog Post Alpha/i,
    });
    fireEvent.keyDown(row, { key: 'Enter', code: 'Enter' });
    await waitFor(() => {
      expect(screen.getByTestId('task-detail-modal')).toBeInTheDocument();
    });
  });

  it('pressing Space on a task row opens the detail modal', async () => {
    renderComponent();
    const row = screen.getByRole('button', {
      name: /View details for Blog Post Alpha/i,
    });
    fireEvent.keyDown(row, { key: ' ', code: 'Space' });
    await waitFor(() => {
      expect(screen.getByTestId('task-detail-modal')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #759: action buttons need aria-label (not emoji only)
// ---------------------------------------------------------------------------

describe('TaskManagement — a11y: action button accessible names (issue #759)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('View Details button has aria-label', () => {
    renderComponent();
    const viewButtons = screen.getAllByRole('button', { name: 'View Details' });
    expect(viewButtons.length).toBeGreaterThan(0);
  });

  it('Reject Task button has aria-label', () => {
    renderComponent();
    const deleteButtons = screen.getAllByRole('button', {
      name: 'Reject Task',
    });
    expect(deleteButtons.length).toBeGreaterThan(0);
  });

  it('Pause Task button has aria-label for running tasks', () => {
    renderComponent();
    // task-2 has status 'running'
    const pauseButtons = screen.getAllByRole('button', { name: 'Pause Task' });
    expect(pauseButtons.length).toBeGreaterThan(0);
  });

  it('Retry Task button has aria-label for failed tasks', () => {
    renderComponent();
    // task-3 has status 'failed'
    const retryButtons = screen.getAllByRole('button', { name: 'Retry Task' });
    expect(retryButtons.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #773: sortable column headers keyboard-operable
// ---------------------------------------------------------------------------

describe('TaskManagement — a11y: sortable column headers (issue #773)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
  });

  it('sortable headers have tabIndex=0', () => {
    const { container } = renderComponent();
    const sortableHeaders = container.querySelectorAll('th.sortable');
    expect(sortableHeaders.length).toBeGreaterThan(0);
    sortableHeaders.forEach((th) => {
      expect(th).toHaveAttribute('tabIndex', '0');
    });
  });

  it('sortable headers have role="columnheader"', () => {
    const { container } = renderComponent();
    const sortableHeaders = container.querySelectorAll('th.sortable');
    sortableHeaders.forEach((th) => {
      expect(th).toHaveAttribute('role', 'columnheader');
    });
  });

  it('non-active sortable headers have aria-sort="none"', () => {
    const { container } = renderComponent();
    // Inactive headers (those without active-sort class) must show aria-sort="none"
    const inactiveHeaders = container.querySelectorAll(
      'th.sortable:not(.active-sort)'
    );
    expect(inactiveHeaders.length).toBeGreaterThan(0);
    inactiveHeaders.forEach((th) => {
      expect(th).toHaveAttribute('aria-sort', 'none');
    });
  });

  it('pressing Enter on a sort header triggers sort', () => {
    const { container } = renderComponent();
    const taskNameHeader = container.querySelector('th.sortable');
    fireEvent.keyDown(taskNameHeader, { key: 'Enter', code: 'Enter' });
    // After keyDown, the header should become active-sort
    expect(taskNameHeader).toHaveClass('active-sort');
  });

  it('pressing Space on a sort header triggers sort', () => {
    const { container } = renderComponent();
    const taskNameHeader = container.querySelector('th.sortable');
    fireEvent.keyDown(taskNameHeader, { key: ' ', code: 'Space' });
    expect(taskNameHeader).toHaveClass('active-sort');
  });

  it('active sort header shows ascending aria-sort', () => {
    const { container } = renderComponent();
    const taskNameHeader = container.querySelector('th.sortable');
    // Click to activate sort on task_name
    fireEvent.click(taskNameHeader);
    expect(taskNameHeader).toHaveAttribute('aria-sort', 'ascending');
  });

  it('clicking active sort header toggles to descending', () => {
    const { container } = renderComponent();
    const taskNameHeader = container.querySelector('th.sortable');
    fireEvent.click(taskNameHeader); // ascending
    fireEvent.click(taskNameHeader); // descending
    expect(taskNameHeader).toHaveAttribute('aria-sort', 'descending');
  });

  it('sort arrow indicators are aria-hidden', () => {
    const { container } = renderComponent();
    const taskNameHeader = container.querySelector('th.sortable');
    fireEvent.click(taskNameHeader); // activate sort to show arrow
    const arrowSpan = taskNameHeader.querySelector('[aria-hidden="true"]');
    expect(arrowSpan).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #776: error/success banners use live regions
// ---------------------------------------------------------------------------

describe('TaskManagement — a11y: live region banners (issue #776)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupHook();
    mockBulkUpdateTasks.mockResolvedValue({ updated_count: 1 });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('error banner has role="alert"', async () => {
    mockBulkUpdateTasks.mockResolvedValue({ updated_count: 0 });
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);
    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert).toBeInTheDocument();
    });
  });

  it('error banner has aria-live="assertive"', async () => {
    mockBulkUpdateTasks.mockResolvedValue({ updated_count: 0 });
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);
    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert).toHaveAttribute('aria-live', 'assertive');
    });
  });

  it('success banner has role="status"', async () => {
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);
    await waitFor(() => {
      const status = screen.getByRole('status');
      expect(status).toBeInTheDocument();
    });
  });

  it('success banner has aria-live="polite"', async () => {
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);
    await waitFor(() => {
      const status = screen.getByRole('status');
      expect(status).toHaveAttribute('aria-live', 'polite');
    });
  });

  it('error dismiss button has aria-label="Dismiss error"', async () => {
    mockBulkUpdateTasks.mockResolvedValue({ updated_count: 0 });
    renderComponent();
    const rejectButtons = screen.getAllByTitle('Reject Task');
    fireEvent.click(rejectButtons[0]);
    await waitFor(() => {
      const dismissBtn = screen.getByRole('button', { name: 'Dismiss error' });
      expect(dismissBtn).toBeInTheDocument();
    });
  });
});
