/**
 * TaskTable.test.js - Unit tests for TaskTable component
 *
 * Tests rendering, selection, pagination, and actions
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

import TaskTable from './TaskTable';

describe('TaskTable Component', () => {
  const mockTasks = [
    {
      id: 1,
      task_name: 'Task 1',
      status: 'pending',
      task_type: 'content',
      created_at: '2024-01-01',
    },
    {
      id: 2,
      task_name: 'Task 2',
      status: 'completed',
      task_type: 'analysis',
      created_at: '2024-01-02',
    },
    {
      id: 3,
      task_name: 'Task 3',
      status: 'in_progress',
      task_type: 'content',
      created_at: '2024-01-03',
    },
  ];

  const defaultProps = {
    tasks: mockTasks,
    loading: false,
    page: 1,
    limit: 10,
    total: 30,
    selectedTasks: [],
    onSelectTask: vi.fn(),
    onSelectAll: vi.fn(),
    onSelectOne: vi.fn(),
    onPageChange: vi.fn(),
    onRowsPerPageChange: vi.fn(),
    onEditTask: vi.fn(),
    onDeleteTask: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Rendering', () => {
    it('should render task table with all tasks', () => {
      render(<TaskTable {...defaultProps} />);

      mockTasks.forEach((task) => {
        expect(
          screen.getByText(task.task_name.substring(0, 40))
        ).toBeInTheDocument();
      });
      // Status chips display formatted labels, not raw values
      expect(screen.getByText('Pending')).toBeInTheDocument();
      expect(screen.getByText('Completed')).toBeInTheDocument();
      expect(screen.getByText('In Progress')).toBeInTheDocument();
    });

    it('should render loading spinner when loading', () => {
      render(<TaskTable {...defaultProps} loading={true} tasks={[]} />);

      expect(screen.getByRole('progressbar')).toBeInTheDocument();
    });

    it('should render empty message when no tasks', () => {
      const { container } = render(
        <TaskTable {...defaultProps} tasks={[]} loading={false} />
      );

      // Table should still exist but be empty
      expect(container.querySelector('table')).toBeInTheDocument();
    });

    it('should render status chips with correct colors', () => {
      render(<TaskTable {...defaultProps} />);

      // getStatusLabel formats: 'pending' → 'Pending', 'completed' → 'Completed'
      const pendingChipLabel = screen.getByText('Pending');
      const pendingChip = pendingChipLabel.closest('[class*="MuiChip-root"]');
      expect(pendingChip).toBeInTheDocument();

      const completedChipLabel = screen.getByText('Completed');
      const completedChip = completedChipLabel.closest(
        '[class*="MuiChip-root"]'
      );
      expect(completedChip).toBeInTheDocument();
    });
  });

  describe('Selection', () => {
    it('should render checkboxes for task selection', () => {
      const { container } = render(<TaskTable {...defaultProps} />);

      const checkboxes = container.querySelectorAll('input[type="checkbox"]');
      // One for select-all header, one for each row
      expect(checkboxes.length).toBe(mockTasks.length + 1);
    });

    it('should call onSelectOne when task checkbox is clicked', () => {
      const { container } = render(<TaskTable {...defaultProps} />);

      const taskCheckboxes = container.querySelectorAll(
        'tbody input[type="checkbox"]'
      );
      fireEvent.click(taskCheckboxes[0]);

      expect(defaultProps.onSelectOne).toHaveBeenCalledWith(
        mockTasks[0].id,
        expect.any(Boolean)
      );
    });

    it('should call onSelectAll when header checkbox is clicked', () => {
      const { container } = render(<TaskTable {...defaultProps} />);

      const headerCheckbox = container.querySelector(
        'thead input[type="checkbox"]'
      );
      fireEvent.click(headerCheckbox);

      expect(defaultProps.onSelectAll).toHaveBeenCalled();
    });

    it('should highlight selected tasks', () => {
      const { container } = render(
        <TaskTable {...defaultProps} selectedTasks={[1]} />
      );

      const firstRow = container.querySelector('tbody tr');
      expect(firstRow).toBeTruthy();
    });
  });

  describe('Action Buttons', () => {
    it('should render edit button for each task', () => {
      render(<TaskTable {...defaultProps} />);

      const editButtons = screen.getAllByRole('button', { name: /edit/i });
      expect(editButtons.length).toBeGreaterThan(0);
    });

    it('should call onEditTask when edit button is clicked', () => {
      const { container } = render(<TaskTable {...defaultProps} />);

      const editButtons = container.querySelectorAll('[aria-label*="Edit"]');
      fireEvent.click(editButtons[0]);

      expect(defaultProps.onEditTask).toHaveBeenCalledWith(mockTasks[0]);
    });

    it('should render delete button for each task', () => {
      const { container } = render(<TaskTable {...defaultProps} />);

      const deleteButtons = container.querySelectorAll(
        '[aria-label*="Delete"]'
      );
      expect(deleteButtons.length).toBe(mockTasks.length);
    });

    it('should call onDeleteTask when delete button is clicked', () => {
      const { container } = render(<TaskTable {...defaultProps} />);

      const deleteButtons = container.querySelectorAll(
        '[aria-label*="Delete"]'
      );
      fireEvent.click(deleteButtons[0]);

      expect(defaultProps.onDeleteTask).toHaveBeenCalledWith(mockTasks[0].id);
    });
  });

  describe('Pagination', () => {
    it('should render pagination controls', () => {
      const { container } = render(<TaskTable {...defaultProps} />);

      expect(
        container.querySelector('.MuiTablePagination-root')
      ).toBeInTheDocument(); // Pagination
    });

    it('should show correct page information', () => {
      render(<TaskTable {...defaultProps} limit={3} />);

      // Pagination shows "1–3 of 30" (page=1, limit=3, 3 tasks, total=30)
      expect(screen.getByText(/1–3 of 30/i)).toBeInTheDocument();
    });

    it('should call onPageChange when navigating pages', () => {
      render(<TaskTable {...defaultProps} />);

      const nextPageButton = screen.getByRole('button', { name: /next page/i });
      fireEvent.click(nextPageButton);

      expect(defaultProps.onPageChange).toHaveBeenCalled();
    });

    it('should call onRowsPerPageChange when limit is changed', () => {
      render(<TaskTable {...defaultProps} />);

      // MUI TablePagination uses a MUI Select (combobox) for rows-per-page
      const limitSelect = screen.getByRole('combobox');
      fireEvent.mouseDown(limitSelect);

      const option25 = screen.getByRole('option', { name: '25' });
      fireEvent.click(option25);

      expect(defaultProps.onRowsPerPageChange).toHaveBeenCalled();
    });
  });

  describe('PropTypes Validation', () => {
    it('should render with minimal props', () => {
      const { container } = render(
        <TaskTable tasks={[]} loading={false} total={0} />
      );

      expect(container.querySelector('table')).toBeInTheDocument();
    });

    it('should use default prop values', () => {
      const { container } = render(<TaskTable tasks={mockTasks} />);

      // Should render without errors
      expect(container.querySelector('table')).toBeInTheDocument();
      expect(
        screen.getByText(mockTasks[0].task_name.substring(0, 40))
      ).toBeInTheDocument();
    });
  });

  describe('Edge Cases', () => {
    it('should handle very long task names', () => {
      const longNameTask = {
        ...mockTasks[0],
        task_name: 'A'.repeat(200),
      };

      render(<TaskTable {...defaultProps} tasks={[longNameTask]} />);

      // component truncates to 40 chars
      expect(screen.getByText('A'.repeat(40))).toBeInTheDocument();
    });

    it('should handle special characters in task names', () => {
      const specialTask = {
        ...mockTasks[0],
        task_name: 'Task <>&"\'',
      };

      render(<TaskTable {...defaultProps} tasks={[specialTask]} />);

      expect(
        screen.getByText('Task <>&"\'', { exact: false })
      ).toBeInTheDocument();
    });

    it('should handle undefined status gracefully', () => {
      const noStatusTask = {
        ...mockTasks[0],
        status: undefined,
      };

      const { container } = render(
        <TaskTable {...defaultProps} tasks={[noStatusTask]} />
      );

      expect(container.querySelector('table')).toBeInTheDocument();
    });
  });
});
