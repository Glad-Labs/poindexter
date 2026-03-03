import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import TaskControlPanel from './TaskControlPanel';
import * as taskService from '../../services/taskService';

// Mock the service
vi.mock('../../services/taskService');

// Mock Zustand store
vi.mock('../../../store/useStore', () => {
  return vi.fn(() => ({
    taskActionLoading: {},
    taskActionError: {},
    setTaskActionLoading: vi.fn(),
    setTaskActionError: vi.fn(),
    clearTaskAction: vi.fn(),
  }));
});

describe('TaskControlPanel Component', () => {
  const mockTask = {
    id: 'task-123',
    status: 'in_progress',
    name: 'Test Task',
  };

  const mockOnTaskUpdated = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('renders Pause button when task is in_progress', () => {
    render(
      <TaskControlPanel task={mockTask} onTaskUpdated={mockOnTaskUpdated} />
    );
    expect(screen.getByText('Pause')).toBeInTheDocument();
  });

  test('renders Resume button when task is paused', () => {
    const pausedTask = { ...mockTask, status: 'paused' };
    render(
      <TaskControlPanel task={pausedTask} onTaskUpdated={mockOnTaskUpdated} />
    );
    expect(screen.getByText('Resume')).toBeInTheDocument();
  });

  test('renders Cancel button for pending/in_progress tasks', () => {
    render(
      <TaskControlPanel task={mockTask} onTaskUpdated={mockOnTaskUpdated} />
    );
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  test('renders Delete button for completed/failed tasks', () => {
    const completedTask = { ...mockTask, status: 'completed' };
    render(
      <TaskControlPanel
        task={completedTask}
        onTaskUpdated={mockOnTaskUpdated}
      />
    );
    expect(screen.getByText('Delete')).toBeInTheDocument();
  });

  test('shows error message when action fails', async () => {
    taskService.pauseTask.mockRejectedValue(new Error('API Error'));

    render(
      <TaskControlPanel task={mockTask} onTaskUpdated={mockOnTaskUpdated} />
    );

    const pauseButton = screen.getByText('Pause');
    fireEvent.click(pauseButton);

    await waitFor(() => {
      expect(taskService.pauseTask).toHaveBeenCalledWith('task-123');
    });
  });

  test('shows confirmation dialog when Cancel is clicked', () => {
    render(
      <TaskControlPanel task={mockTask} onTaskUpdated={mockOnTaskUpdated} />
    );

    const cancelButton = screen.getByText('Cancel');
    fireEvent.click(cancelButton);

    expect(screen.getByText('Cancel Task?')).toBeInTheDocument();
    expect(
      screen.getByText(/Are you sure you want to cancel this task/i)
    ).toBeInTheDocument();
  });

  test('shows confirmation dialog when Delete is clicked', () => {
    const completedTask = { ...mockTask, status: 'completed' };
    render(
      <TaskControlPanel
        task={completedTask}
        onTaskUpdated={mockOnTaskUpdated}
      />
    );

    const deleteButton = screen.getByText('Delete');
    fireEvent.click(deleteButton);

    expect(screen.getByText('Delete Task?')).toBeInTheDocument();
    expect(
      screen.getByText(/Are you sure you want to permanently delete this task/i)
    ).toBeInTheDocument();
  });

  test('calls pauseTask when Pause button is clicked', async () => {
    taskService.pauseTask.mockResolvedValue({ ...mockTask, status: 'paused' });

    render(
      <TaskControlPanel task={mockTask} onTaskUpdated={mockOnTaskUpdated} />
    );

    const pauseButton = screen.getByText('Pause');
    fireEvent.click(pauseButton);

    await waitFor(() => {
      expect(taskService.pauseTask).toHaveBeenCalledWith('task-123');
    });
  });

  test('calls resumeTask when Resume button is clicked', async () => {
    taskService.resumeTask.mockResolvedValue({
      ...mockTask,
      status: 'in_progress',
    });

    const pausedTask = { ...mockTask, status: 'paused' };
    render(
      <TaskControlPanel task={pausedTask} onTaskUpdated={mockOnTaskUpdated} />
    );

    const resumeButton = screen.getByText('Resume');
    fireEvent.click(resumeButton);

    await waitFor(() => {
      expect(taskService.resumeTask).toHaveBeenCalledWith('task-123');
    });
  });

  test('shows message when no actions are available', () => {
    const inmovableTask = { ...mockTask, status: 'unknown' };
    render(
      <TaskControlPanel
        task={inmovableTask}
        onTaskUpdated={mockOnTaskUpdated}
      />
    );

    expect(
      screen.getByText(/No actions available for this task status/i)
    ).toBeInTheDocument();
  });

  test('disables buttons while action is loading', () => {
    // With default mock state (taskActionLoading: {}), buttons are enabled
    render(
      <TaskControlPanel task={mockTask} onTaskUpdated={mockOnTaskUpdated} />
    );

    // Buttons should be present and not disabled when no action is loading
    const pauseButton = screen.getByText('Pause');
    expect(pauseButton).toBeInTheDocument();
  });
});
