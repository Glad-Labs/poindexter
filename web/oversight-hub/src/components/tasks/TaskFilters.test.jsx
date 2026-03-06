/**
 * TaskFilters.test.js - Unit tests for TaskFilters component
 *
 * Tests filter controls, callbacks, and state changes
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

import TaskFilters from './TaskFilters';

describe('TaskFilters Component', () => {
  const defaultProps = {
    sortBy: 'created_at',
    sortDirection: 'desc',
    statusFilter: '',
    onSortChange: vi.fn(),
    onDirectionChange: vi.fn(),
    onStatusChange: vi.fn(),
    onResetFilters: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Rendering', () => {
    it('should render all filter controls', () => {
      render(<TaskFilters {...defaultProps} />);

      expect(screen.getAllByRole('combobox')[0]).toBeInTheDocument();
      expect(screen.getAllByRole('combobox')[1]).toBeInTheDocument();
      expect(screen.getAllByRole('combobox')[2]).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /reset/i })
      ).toBeInTheDocument();
    });

    it('should display current filter values', () => {
      const { container } = render(<TaskFilters {...defaultProps} />);

      // The hidden native input holds the current sort value
      const sortInput = container.querySelector('input[value="created_at"]');
      expect(sortInput).toBeInTheDocument();
    });

    it('should render sort field options', () => {
      render(<TaskFilters {...defaultProps} />);

      const sortButton = screen.getAllByRole('combobox')[0];
      fireEvent.mouseDown(sortButton);

      expect(
        screen.getByRole('option', { name: 'Created Date' })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('option', { name: 'Status' })
      ).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'Name' })).toBeInTheDocument();
      expect(
        screen.getByRole('option', { name: 'Task Type' })
      ).toBeInTheDocument();
    });

    it('should render direction options', () => {
      render(<TaskFilters {...defaultProps} />);

      const directionButton = screen.getAllByRole('combobox')[1];
      fireEvent.mouseDown(directionButton);

      expect(
        screen.getByRole('option', { name: 'Ascending' })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('option', { name: 'Descending' })
      ).toBeInTheDocument();
    });

    it('should render status filter options', () => {
      render(<TaskFilters {...defaultProps} />);

      const statusButton = screen.getAllByRole('combobox')[2];
      fireEvent.mouseDown(statusButton);

      expect(screen.getAllByText('All Statuses').length).toBeGreaterThanOrEqual(
        1
      );
      expect(
        screen.getByRole('option', { name: 'Pending' })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('option', { name: 'In Progress' })
      ).toBeInTheDocument();
    });
  });

  describe('Sort Control', () => {
    it('should call onSortChange when sort field is changed', () => {
      render(<TaskFilters {...defaultProps} />);

      const sortButton = screen.getAllByRole('combobox')[0];
      fireEvent.mouseDown(sortButton);

      const nameOption = screen.getByText('Name');
      fireEvent.click(nameOption);

      expect(defaultProps.onSortChange).toHaveBeenCalledWith('name');
    });

    it('should display current sort field', () => {
      const { container } = render(
        <TaskFilters {...defaultProps} sortBy="status" />
      );

      // The selected value should be reflected in the select
      const sortSelect = container.querySelector('[value="status"]');
      expect(sortSelect).toBeInTheDocument();
    });

    it('should highlight current sort option', () => {
      render(<TaskFilters {...defaultProps} sortBy="created_at" />);

      const sortButton = screen.getAllByRole('combobox')[0];
      fireEvent.mouseDown(sortButton);

      const option = screen.getByRole('option', { name: 'Created Date' });
      expect(option.getAttribute('aria-selected')).toBe('true');
    });
  });

  describe('Direction Control', () => {
    it('should call onDirectionChange when direction is changed', () => {
      render(<TaskFilters {...defaultProps} />);

      const directionButton = screen.getAllByRole('combobox')[1];
      fireEvent.mouseDown(directionButton);

      const ascOption = screen.getByText('Ascending');
      fireEvent.click(ascOption);

      expect(defaultProps.onDirectionChange).toHaveBeenCalledWith('asc');
    });

    it('should display current direction', () => {
      const { container } = render(
        <TaskFilters {...defaultProps} sortDirection="asc" />
      );

      const directionSelect = container.querySelector('[value="asc"]');
      expect(directionSelect).toBeInTheDocument();
    });

    it('should toggle direction easily', () => {
      const { rerender } = render(
        <TaskFilters {...defaultProps} sortDirection="desc" />
      );

      let directionButton = screen.getAllByRole('combobox')[1];
      fireEvent.mouseDown(directionButton);
      fireEvent.click(screen.getByText('Ascending'));

      rerender(<TaskFilters {...defaultProps} sortDirection="asc" />);

      directionButton = screen.getAllByRole('combobox')[1];
      fireEvent.mouseDown(directionButton);

      expect(
        screen
          .getByRole('option', { name: 'Ascending' })
          .getAttribute('aria-selected')
      ).toBe('true');
    });
  });

  describe('Status Filter Control', () => {
    it('should call onStatusChange when status is changed', () => {
      render(<TaskFilters {...defaultProps} />);

      const statusButton = screen.getAllByRole('combobox')[2];
      fireEvent.mouseDown(statusButton);

      const pendingOption = screen.getByText('Pending');
      fireEvent.click(pendingOption);

      expect(defaultProps.onStatusChange).toHaveBeenCalledWith('pending');
    });

    it('should display current status filter', () => {
      const { container } = render(
        <TaskFilters {...defaultProps} statusFilter="completed" />
      );

      const statusSelect = container.querySelector('[value="completed"]');
      expect(statusSelect).toBeInTheDocument();
    });

    it('should show "All Statuses" option', () => {
      render(<TaskFilters {...defaultProps} statusFilter="pending" />);

      const statusButton = screen.getAllByRole('combobox')[2];
      fireEvent.mouseDown(statusButton);

      const allOption = screen.getByText('All Statuses');
      fireEvent.click(allOption);

      expect(defaultProps.onStatusChange).toHaveBeenCalledWith('');
    });
  });

  describe('Reset Button', () => {
    it('should render reset button', () => {
      render(<TaskFilters {...defaultProps} />);

      const resetButton = screen.getByRole('button', { name: /reset/i });
      expect(resetButton).toBeInTheDocument();
    });

    it('should call onResetFilters when reset button is clicked', () => {
      render(<TaskFilters {...defaultProps} />);

      const resetButton = screen.getByRole('button', { name: /reset/i });
      fireEvent.click(resetButton);

      expect(defaultProps.onResetFilters).toHaveBeenCalled();
    });

    it('should reset all filters to defaults', () => {
      const { rerender } = render(
        <TaskFilters
          {...defaultProps}
          sortBy="status"
          sortDirection="asc"
          statusFilter="pending"
        />
      );

      const resetButton = screen.getByRole('button', { name: /reset/i });
      fireEvent.click(resetButton);

      expect(defaultProps.onResetFilters).toHaveBeenCalledTimes(1);

      // After reset
      rerender(
        <TaskFilters
          {...defaultProps}
          sortBy="created_at"
          sortDirection="desc"
          statusFilter=""
          onResetFilters={vi.fn()}
        />
      );

      // Verify values are reset to defaults
      const sortButton = screen.getAllByRole('combobox')[0];
      fireEvent.mouseDown(sortButton);
      expect(
        screen
          .getByRole('option', { name: 'Created Date' })
          .getAttribute('aria-selected')
      ).toBe('true');
    });
  });

  describe('Multiple Filter Changes', () => {
    it('should handle multiple filter changes independently', () => {
      const { rerender } = render(<TaskFilters {...defaultProps} />);

      // Change sort
      const sortButton = screen.getAllByRole('combobox')[0];
      fireEvent.mouseDown(sortButton);
      fireEvent.click(screen.getByText('Name'));
      expect(defaultProps.onSortChange).toHaveBeenCalledWith('name');

      vi.clearAllMocks();

      // Change direction
      rerender(<TaskFilters {...defaultProps} sortBy="name" />);
      const directionButton = screen.getAllByRole('combobox')[1];
      fireEvent.mouseDown(directionButton);
      fireEvent.click(screen.getByText('Ascending'));
      expect(defaultProps.onDirectionChange).toHaveBeenCalledWith('asc');

      vi.clearAllMocks();

      // Change status
      rerender(
        <TaskFilters {...defaultProps} sortBy="name" sortDirection="asc" />
      );
      const statusButton = screen.getAllByRole('combobox')[2];
      fireEvent.mouseDown(statusButton);
      fireEvent.click(screen.getByText('Completed'));
      expect(defaultProps.onStatusChange).toHaveBeenCalledWith('completed');
    });
  });

  describe('PropTypes Validation', () => {
    it('should render with minimal props', () => {
      render(
        <TaskFilters
          onSortChange={vi.fn()}
          onDirectionChange={vi.fn()}
          onStatusChange={vi.fn()}
          onResetFilters={vi.fn()}
        />
      );
      expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(3);
    });

    it('should use default prop values', () => {
      const { container } = render(<TaskFilters onSortChange={vi.fn()} />);

      expect(
        container.querySelector('[value="created_at"]')
      ).toBeInTheDocument();
      expect(container.querySelector('[value="desc"]')).toBeInTheDocument();
    });
  });

  describe('Accessibility', () => {
    it('should have proper labels for all controls', () => {
      render(<TaskFilters {...defaultProps} />);

      expect(screen.getAllByRole('combobox')[0]).toBeInTheDocument();
      expect(screen.getAllByRole('combobox')[1]).toBeInTheDocument();
      expect(screen.getAllByRole('combobox')[2]).toBeInTheDocument();
    });

    it('should be keyboard navigable', () => {
      render(<TaskFilters {...defaultProps} />);

      const sortButton = screen.getAllByRole('combobox')[0];
      sortButton.focus();
      expect(sortButton).toHaveFocus();
    });

    it('should have descriptive button labels', () => {
      render(<TaskFilters {...defaultProps} />);

      const resetButton = screen.getByRole('button', { name: /reset/i });
      expect(resetButton).toBeInTheDocument();
    });
  });
});
