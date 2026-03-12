/**
 * Tests for components/dashboard/LiveTaskMonitor.jsx
 *
 * Covers:
 * - Renders task name and task ID
 * - Shows PENDING status chip initially
 * - Shows progress bar with 0%
 * - Shows "Initializing" as current step
 * - Disconnected warning when isConnected=false
 * - Progress update via useTaskProgress callback
 * - Error message displayed when progress has error
 * - Elapsed time shown when > 0
 * - Time formatting
 */

import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock WebSocketContext
let capturedProgressCallback = null;

vi.mock('../../../context/WebSocketContext', () => ({
  useWebSocket: vi.fn(() => ({ isConnected: true })),
  useTaskProgress: vi.fn((taskId, cb) => {
    capturedProgressCallback = cb;
  }),
}));

// Mock notificationService
vi.mock('../../../services/notificationService', () => ({
  notificationService: {
    notify: vi.fn(),
  },
}));

// Mock ErrorBoundary (default export wrapping)
vi.mock('../../ErrorBoundary', () => ({
  default: ({ children }) => <>{children}</>,
}));

import { LiveTaskMonitor } from '../LiveTaskMonitor';
import { useWebSocket } from '../../../context/WebSocketContext';
import { notificationService } from '../../../services/notificationService';

const TASK_ID = 'task-abcd-1234-efgh-5678';
const TASK_NAME = 'Blog Post Generation';

describe('LiveTaskMonitor — initial render', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedProgressCallback = null;
  });

  it('renders the task name', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(screen.getByText(TASK_NAME)).toBeInTheDocument();
  });

  it('renders a truncated task ID in subheader', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(document.body.textContent).toContain('task-abc');
  });

  it('shows PENDING status chip', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(screen.getByText('PENDING')).toBeInTheDocument();
  });

  it('shows 0% progress initially', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  it('shows Initializing as current step', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(screen.getByText('Initializing')).toBeInTheDocument();
  });

  it('shows Waiting to start status message', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(screen.getByText('Waiting to start')).toBeInTheDocument();
  });

  it('does not show disconnected warning when connected', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(
      screen.queryByText(/Real-time updates disconnected/i)
    ).not.toBeInTheDocument();
  });

  it('shows disconnected warning when isConnected=false', () => {
    vi.mocked(useWebSocket).mockReturnValue({ isConnected: false });
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    expect(
      screen.getByText(/Real-time updates disconnected/i)
    ).toBeInTheDocument();
    vi.mocked(useWebSocket).mockReturnValue({ isConnected: true });
  });
});

describe('LiveTaskMonitor — progress updates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedProgressCallback = null;
  });

  it('updates progress bar when progress update received', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    act(() => {
      capturedProgressCallback({
        status: 'RUNNING',
        progress: 45,
        currentStep: 'Research phase',
        message: 'Gathering data',
      });
    });
    expect(screen.getByText('45%')).toBeInTheDocument();
    expect(screen.getByText('Research phase')).toBeInTheDocument();
    expect(screen.getByText('Gathering data')).toBeInTheDocument();
  });

  it('updates status chip when status changes', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    act(() => {
      capturedProgressCallback({ status: 'COMPLETED', progress: 100 });
    });
    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
  });

  it('shows error alert when progress.error is set', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    act(() => {
      capturedProgressCallback({
        status: 'FAILED',
        error: 'LLM API timeout after 30s',
      });
    });
    expect(screen.getByText('LLM API timeout after 30s')).toBeInTheDocument();
  });

  it('calls notificationService.notify when status changes', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    act(() => {
      capturedProgressCallback({ status: 'RUNNING' });
    });
    expect(notificationService.notify).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'info', title: 'Task Started' })
    );
  });

  it('shows elapsed time when elapsedTime > 0', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    act(() => {
      capturedProgressCallback({ status: 'RUNNING', elapsedTime: 75 });
    });
    // 75s = 1m 15s
    expect(screen.getByText(/1m 15s/)).toBeInTheDocument();
  });

  it('shows step progress when totalSteps is set', () => {
    render(<LiveTaskMonitor taskId={TASK_ID} taskName={TASK_NAME} />);
    act(() => {
      capturedProgressCallback({
        status: 'RUNNING',
        totalSteps: 6,
        completedSteps: 3,
        currentStep: 'QA Critique',
      });
    });
    expect(screen.getByText(/Step 4 of 6/)).toBeInTheDocument();
  });
});
