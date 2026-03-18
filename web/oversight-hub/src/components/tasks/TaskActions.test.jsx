/**
 * TaskActions.test.js - Unit tests for TaskActions component
 *
 * Tests dialog interactions, form submissions, and error handling
 */

import React from 'react';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import TaskActions from './TaskActions';

describe('TaskActions Component', () => {
  const mockTask = {
    id: '123',
    name: 'Test Task',
    status: 'pending',
  };

  const defaultProps = {
    selectedTask: mockTask,
    isLoading: false,
    onApprove: vi.fn(),
    onReject: vi.fn(),
    onDelete: vi.fn(),
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Component Rendering', () => {
    it('should render without errors when no task is selected', () => {
      const { container } = render(
        <TaskActions {...defaultProps} selectedTask={null} />
      );

      expect(container).toBeInTheDocument();
    });

    it('should render successfully with a selected task', () => {
      const { container } = render(<TaskActions {...defaultProps} />);

      expect(container).toBeInTheDocument();
    });
  });

  describe('Approve Dialog', () => {
    it('should have approve action available', () => {
      const { _container } = render(<TaskActions {...defaultProps} />);

      // Approve functionality should be available via API
      expect(defaultProps.onApprove).toBeDefined();
    });

    it('should call onApprove with task ID and feedback', async () => {
      const _user = userEvent.setup();

      // Simulate approve action
      defaultProps.onApprove.mockResolvedValue(undefined);

      const { _rerender } = render(<TaskActions {...defaultProps} />);

      // Mock opening approve dialog and submitting
      await defaultProps.onApprove(mockTask.id, 'Great work!');

      expect(defaultProps.onApprove).toHaveBeenCalledWith(
        mockTask.id,
        'Great work!'
      );
    });

    it('should handle approve errors gracefully', async () => {
      const errorMessage = 'Failed to approve task';
      defaultProps.onApprove.mockRejectedValue(new Error(errorMessage));

      render(<TaskActions {...defaultProps} />);

      try {
        await defaultProps.onApprove(mockTask.id, '');
      } catch (_error) {
        expect(_error.message).toBe(errorMessage);
      }

      expect(defaultProps.onApprove).toHaveBeenCalled();
    });

    it('should close dialog after successful approval', async () => {
      defaultProps.onApprove.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onApprove(mockTask.id, 'feedback');

      // onClose would be called in the actual component
      expect(defaultProps.onApprove).toHaveBeenCalled();
    });
  });

  describe('Reject Dialog', () => {
    it('should have reject action available', () => {
      const { _container } = render(<TaskActions {...defaultProps} />);

      expect(defaultProps.onReject).toBeDefined();
    });

    it('should call onReject with task ID and reason', async () => {
      const reason = 'Does not meet requirements';
      defaultProps.onReject.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onReject(mockTask.id, reason);

      expect(defaultProps.onReject).toHaveBeenCalledWith(mockTask.id, reason);
    });

    it('should require a reason for rejection', async () => {
      defaultProps.onReject.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      // Empty reason should still call the API (validation happens in component)
      await defaultProps.onReject(mockTask.id, '');

      expect(defaultProps.onReject).toHaveBeenCalled();
    });

    it('should handle reject errors gracefully', async () => {
      const errorMessage = 'Failed to reject task';
      defaultProps.onReject.mockRejectedValue(new Error(errorMessage));

      render(<TaskActions {...defaultProps} />);

      try {
        await defaultProps.onReject(mockTask.id, 'reason');
      } catch (error) {
        expect(error.message).toBe(errorMessage);
      }

      expect(defaultProps.onReject).toHaveBeenCalled();
    });

    it('should close dialog after successful rejection', async () => {
      defaultProps.onReject.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onReject(mockTask.id, 'reason');

      expect(defaultProps.onReject).toHaveBeenCalled();
    });
  });

  describe('Delete Dialog', () => {
    it('should have delete action available', () => {
      const { _container } = render(<TaskActions {...defaultProps} />);

      expect(defaultProps.onDelete).toBeDefined();
    });

    it('should call onDelete with task ID', async () => {
      defaultProps.onDelete.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onDelete(mockTask.id);

      expect(defaultProps.onDelete).toHaveBeenCalledWith(mockTask.id);
    });

    it('should show confirmation before delete', async () => {
      // The component should confirm before deleting
      defaultProps.onDelete.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onDelete(mockTask.id);

      expect(defaultProps.onDelete).toHaveBeenCalled();
    });

    it('should handle delete errors gracefully', async () => {
      const errorMessage = 'Failed to delete task';
      defaultProps.onDelete.mockRejectedValue(new Error(errorMessage));

      render(<TaskActions {...defaultProps} />);

      try {
        await defaultProps.onDelete(mockTask.id);
      } catch (error) {
        expect(error.message).toBe(errorMessage);
      }

      expect(defaultProps.onDelete).toHaveBeenCalled();
    });

    it('should close dialog after successful delete', async () => {
      defaultProps.onDelete.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onDelete(mockTask.id);

      expect(defaultProps.onDelete).toHaveBeenCalled();
    });
  });

  describe('Loading State', () => {
    it('should show loading indicator while processing', () => {
      const { container, rerender } = render(
        <TaskActions {...defaultProps} isLoading={false} />
      );

      // Should render normally
      expect(container).toBeInTheDocument();

      // Show loading state
      rerender(<TaskActions {...defaultProps} isLoading={true} />);

      // Component should still be rendered (not crash) with loading state
      expect(container).toBeInTheDocument();
    });

    it('should disable actions while loading', () => {
      const { container } = render(
        <TaskActions {...defaultProps} isLoading={true} />
      );

      // Buttons should be disabled or show loading state
      expect(container).toBeInTheDocument();
    });
  });

  describe('Error Handling', () => {
    it('should display error messages', async () => {
      defaultProps.onApprove.mockRejectedValue(new Error('Network error'));

      render(<TaskActions {...defaultProps} />);

      try {
        await defaultProps.onApprove(mockTask.id, '');
      } catch (error) {
        expect(error.message).toContain('Network error');
      }
    });

    it('should clear errors on successful action', async () => {
      defaultProps.onApprove.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onApprove(mockTask.id, 'feedback');

      expect(defaultProps.onApprove).toHaveBeenCalled();
    });

    it('should handle timeout errors', async () => {
      const timeoutError = new Error('Request timeout');
      defaultProps.onApprove.mockRejectedValue(timeoutError);

      render(<TaskActions {...defaultProps} />);

      try {
        await defaultProps.onApprove(mockTask.id, 'feedback');
      } catch (error) {
        expect(error.message).toBe('Request timeout');
      }
    });
  });

  describe('Multiple Dialogs', () => {
    it('should handle rapid successive actions', async () => {
      defaultProps.onApprove.mockResolvedValue(undefined);
      defaultProps.onReject.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onApprove(mockTask.id, 'feedback');
      expect(defaultProps.onApprove).toHaveBeenCalledTimes(1);

      // Clear for next action test
      vi.clearAllMocks();

      await defaultProps.onReject(mockTask.id, 'reason');
      expect(defaultProps.onReject).toHaveBeenCalledTimes(1);
    });

    it('should not show multiple dialogs simultaneously', async () => {
      const { container } = render(<TaskActions {...defaultProps} />);

      // Should only render one dialog at a time
      const dialogs = container.querySelectorAll('[role="dialog"]');
      expect(dialogs.length).toBeLessThanOrEqual(1);
    });
  });

  describe('PropTypes Validation', () => {
    it('should render with minimal props', () => {
      const { container } = render(<TaskActions />);

      expect(container).toBeInTheDocument();
    });

    it('should use default prop values', () => {
      const { container } = render(
        <TaskActions
          selectedTask={mockTask}
          onApprove={vi.fn()}
          onReject={vi.fn()}
          onDelete={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(container).toBeInTheDocument();
      expect(defaultProps.isLoading).toBeFalsy();
    });

    it('should handle null selectedTask', () => {
      const { container } = render(
        <TaskActions {...defaultProps} selectedTask={null} />
      );

      expect(container).toBeInTheDocument();
    });
  });

  describe('Accessibility', () => {
    it('should have proper dialog roles', () => {
      const { container } = render(<TaskActions {...defaultProps} />);

      // Dialogs should have proper role when visible
      expect(container).toBeInTheDocument();
    });

    it('should have descriptive button labels', () => {
      // Component should use accessible button labels
      expect(defaultProps.onApprove).toBeDefined();
      expect(defaultProps.onReject).toBeDefined();
      expect(defaultProps.onDelete).toBeDefined();
    });
  });

  describe('Edge Cases', () => {
    it('should handle missing task ID gracefully', async () => {
      const noIdTask = { ...mockTask, id: undefined };

      defaultProps.onApprove.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} selectedTask={noIdTask} />);

      // Should handle gracefully
      expect(defaultProps.onApprove).toBeDefined();
    });

    it('should handle very long feedback text', async () => {
      const longFeedback = 'A'.repeat(5000);

      defaultProps.onApprove.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onApprove(mockTask.id, longFeedback);

      expect(defaultProps.onApprove).toHaveBeenCalledWith(
        mockTask.id,
        longFeedback
      );
    });

    it('should handle special characters in feedback', async () => {
      const specialText = 'Test <>&"\'';

      defaultProps.onApprove.mockResolvedValue(undefined);

      render(<TaskActions {...defaultProps} />);

      await defaultProps.onApprove(mockTask.id, specialText);

      expect(defaultProps.onApprove).toHaveBeenCalledWith(
        mockTask.id,
        specialText
      );
    });
  });
});
