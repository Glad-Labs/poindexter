import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

vi.mock('@/lib/logger', () => ({
  default: {
    debug: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    log: vi.fn(),
  },
}));

const { mockUpdateTaskContent } = vi.hoisted(() => ({
  mockUpdateTaskContent: vi.fn(),
}));
vi.mock('../../services/taskService', () => ({
  updateTaskContent: (...args) => mockUpdateTaskContent(...args),
}));

import TaskContentPreview from '../tasks/TaskContentPreview';

const baseTask = {
  id: 'task-123',
  topic: 'Test Topic',
  task_metadata: null,
  result: null,
};

describe('TaskContentPreview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when task is null', () => {
    const { container } = render(<TaskContentPreview task={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('displays the task topic as the title', () => {
    render(<TaskContentPreview task={baseTask} />);
    expect(screen.getByText('Test Topic')).toBeInTheDocument();
  });

  it('shows task id', () => {
    render(<TaskContentPreview task={baseTask} />);
    expect(screen.getByText(/task-123/)).toBeInTheDocument();
  });

  it('shows Edit Content button by default', () => {
    render(<TaskContentPreview task={baseTask} />);
    expect(screen.getByText(/Edit Content/i)).toBeInTheDocument();
  });

  it('shows Preview Mode toggle when not editing', () => {
    render(<TaskContentPreview task={baseTask} />);
    expect(screen.getByText('Preview Mode')).toBeInTheDocument();
  });

  it('enters edit mode when Edit Content is clicked', () => {
    render(<TaskContentPreview task={baseTask} />);
    fireEvent.click(screen.getByText(/Edit Content/i));

    expect(screen.getByText(/Save Changes/i)).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('renders the content section header', () => {
    render(<TaskContentPreview task={baseTask} />);
    // The h3 shows "Content Preview" (or "Content Editor" in edit mode)
    expect(
      screen.getByRole('heading', { name: /Content/i, level: 3 })
    ).toBeInTheDocument();
  });

  it('exits edit mode when Cancel is clicked', () => {
    render(<TaskContentPreview task={baseTask} />);
    fireEvent.click(screen.getByText(/Edit Content/i));

    // Now in edit mode
    expect(screen.getByText('Cancel')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Cancel'));

    // Back to view mode
    expect(screen.getByText(/Edit Content/i)).toBeInTheDocument();
    expect(screen.queryByText('Cancel')).not.toBeInTheDocument();
  });

  it('shows title text field when in edit mode', () => {
    render(<TaskContentPreview task={baseTask} />);
    fireEvent.click(screen.getByText(/Edit Content/i));

    const titleInput = screen.getByLabelText('Title');
    expect(titleInput).toBeInTheDocument();
    expect(titleInput.value).toBe('Test Topic');
  });

  it('shows content textarea when in edit mode', () => {
    render(<TaskContentPreview task={baseTask} />);
    fireEvent.click(screen.getByText(/Edit Content/i));

    const contentArea = screen.getByPlaceholderText(
      /Write your content in Markdown/i
    );
    expect(contentArea).toBeInTheDocument();
  });

  it('saves changes when Save Changes is clicked', async () => {
    mockUpdateTaskContent.mockResolvedValueOnce({
      ...baseTask,
      topic: 'Updated Topic',
    });

    const onTaskUpdate = vi.fn();
    render(<TaskContentPreview task={baseTask} onTaskUpdate={onTaskUpdate} />);

    fireEvent.click(screen.getByText(/Edit Content/i));
    fireEvent.click(screen.getByText(/Save Changes/i));

    await waitFor(() => {
      expect(mockUpdateTaskContent).toHaveBeenCalledWith(
        'task-123',
        expect.objectContaining({
          topic: 'Test Topic',
        })
      );
    });
  });

  it('shows success snackbar after save', async () => {
    mockUpdateTaskContent.mockResolvedValueOnce(baseTask);

    render(<TaskContentPreview task={baseTask} />);
    fireEvent.click(screen.getByText(/Edit Content/i));
    fireEvent.click(screen.getByText(/Save Changes/i));

    await waitFor(() => {
      expect(
        screen.getByText('Changes saved successfully')
      ).toBeInTheDocument();
    });
  });

  it('shows error snackbar when save fails', async () => {
    mockUpdateTaskContent.mockRejectedValueOnce(new Error('Network error'));

    render(<TaskContentPreview task={baseTask} />);
    fireEvent.click(screen.getByText(/Edit Content/i));
    fireEvent.click(screen.getByText(/Save Changes/i));

    await waitFor(() => {
      expect(screen.getByText('Failed to save changes')).toBeInTheDocument();
    });
  });

  it('calls onTaskUpdate callback after successful save', async () => {
    const updatedTask = { ...baseTask, topic: 'New Topic' };
    mockUpdateTaskContent.mockResolvedValueOnce(updatedTask);

    const onTaskUpdate = vi.fn();
    render(<TaskContentPreview task={baseTask} onTaskUpdate={onTaskUpdate} />);

    fireEvent.click(screen.getByText(/Edit Content/i));
    fireEvent.click(screen.getByText(/Save Changes/i));

    await waitFor(() => {
      expect(onTaskUpdate).toHaveBeenCalledWith(updatedTask);
    });
  });

  it('extracts title from markdown content starting with a heading', () => {
    const taskWithContent = {
      ...baseTask,
      result: { draft_content: '# My Article Title\n\nSome content here.' },
    };
    render(<TaskContentPreview task={taskWithContent} />);
    expect(screen.getByText('My Article Title')).toBeInTheDocument();
  });

  it('renders featured image when available in task_metadata', () => {
    const taskWithImage = {
      ...baseTask,
      task_metadata: { featured_image_url: 'https://example.com/image.jpg' },
    };
    render(<TaskContentPreview task={taskWithImage} />);
    const img = screen.getByRole('img', { name: /Featured/ });
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute('src', 'https://example.com/image.jpg');
  });

  it('does not render featured image section when no image url', () => {
    render(<TaskContentPreview task={baseTask} />);
    expect(
      screen.queryByRole('img', { name: /Featured/ })
    ).not.toBeInTheDocument();
  });

  it('shows Saving... while save is in progress', async () => {
    // Return a promise that resolves after assertions
    let resolveUpdate;
    mockUpdateTaskContent.mockReturnValueOnce(
      new Promise((res) => {
        resolveUpdate = res;
      })
    );

    render(<TaskContentPreview task={baseTask} />);
    fireEvent.click(screen.getByText(/Edit Content/i));
    fireEvent.click(screen.getByText(/Save Changes/i));

    expect(screen.getByText('Saving...')).toBeInTheDocument();

    // Resolve and cleanup
    resolveUpdate(baseTask);
    await waitFor(() => {
      expect(screen.queryByText('Saving...')).not.toBeInTheDocument();
    });
  });
});
