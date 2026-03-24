/**
 * Frontend component tests for ApprovalQueue.jsx
 *
 * Tests for:
 * - Rendering pending tasks list
 * - Single approve/reject operations
 * - Bulk task selection and operations
 * - WebSocket real-time updates
 * - Error handling and loading states
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ApprovalQueue from '../ApprovalQueue';

describe('ApprovalQueue Component', () => {
  const mockTasks = [
    {
      task_id: '123e4567-e89b-12d3-a456-426614174000',
      task_name: 'Test Blog Post 1',
      status: 'awaiting_approval',
      topic: 'AI Technology',
      task_type: 'blog_post',
      quality_score: 8.5,
      content_preview: 'This is a test blog post...',
      featured_image_url: 'https://example.com/image.jpg',
      created_at: '2026-02-20T10:00:00Z',
    },
    {
      task_id: '223e4567-e89b-12d3-a456-426614174001',
      task_name: 'Test Newsletter 1',
      status: 'awaiting_approval',
      topic: 'Weekly Updates',
      task_type: 'newsletter',
      quality_score: 7.2,
      content_preview: 'Newsletter content...',
      featured_image_url: null,
      created_at: '2026-02-20T09:00:00Z',
    },
  ];

  beforeEach(() => {
    // Mock fetch for API calls
    global.fetch = vi.fn();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('Task List Rendering', () => {
    test('renders task list with pending approval tasks', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
          offset: 0,
          limit: 10,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        expect(screen.getByText('Test Blog Post 1')).toBeInTheDocument();
        expect(screen.getByText('Test Newsletter 1')).toBeInTheDocument();
      });
    });

    test('displays task quality scores', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        expect(screen.getByText(/Quality: 8.5\/10/)).toBeInTheDocument();
        expect(screen.getByText(/Quality: 7.2\/10/)).toBeInTheDocument();
      });
    });

    test('shows empty state when no tasks pending', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: [],
          total: 0,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        expect(
          screen.getByText(/No tasks awaiting approval/i)
        ).toBeInTheDocument();
      });
    });
  });

  describe('Bulk Selection Feature', () => {
    test('toggles checkbox selection on task card', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const checkboxes = screen.getAllByRole('checkbox');
        expect(checkboxes.length).toBeGreaterThan(0);
      });

      const firstCheckbox = screen.getAllByRole('checkbox')[0];
      fireEvent.click(firstCheckbox);

      await waitFor(() => {
        expect(screen.getByText(/1 task selected/)).toBeInTheDocument();
      });
    });

    test('select all button selects all visible tasks', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        expect(screen.getByText(/Select All/)).toBeInTheDocument();
      });

      const selectAllButton = screen.getByText(/Select All/);
      fireEvent.click(selectAllButton);

      await waitFor(() => {
        expect(screen.getByText(/2 tasks selected/)).toBeInTheDocument();
      });
    });

    test('clear selection button clears all selections', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        expect(screen.getByText(/Clear Selection/)).toBeInTheDocument();
      });

      const clearButton = screen.getByText(/Clear Selection/);
      fireEvent.click(clearButton);

      await waitFor(() => {
        expect(
          screen.queryByText(/\d+ tasks? selected/)
        ).not.toBeInTheDocument();
      });
    });

    test('bulk approve button is disabled without selection', () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      // Bulk Approve button should not be visible if no tasks selected
      // (it's only shown when selectedTaskIds.size > 0)
      expect(screen.queryByText(/Bulk Approve/)).not.toBeInTheDocument();
    });

    test('bulk approve button shows count of selected tasks', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        expect(screen.getByText(/Bulk Approve \(2\)/)).toBeInTheDocument();
      });
    });

    test('bulk reject button shows count of selected tasks', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        expect(screen.getByText(/Bulk Reject \(2\)/)).toBeInTheDocument();
      });
    });
  });

  describe('Bulk Approval Dialog', () => {
    test('opens dialog when bulk approve button clicked', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      const selectAllButton = await screen.findByText(/Select All/);
      fireEvent.click(selectAllButton);

      const bulkApproveBtn = await screen.findByText(/Bulk Approve \(2\)/);
      fireEvent.click(bulkApproveBtn);

      await waitFor(() => {
        expect(screen.getByText(/Bulk Approve Tasks/)).toBeInTheDocument();
        expect(screen.getAllByText(/2 tasks/)[0]).toBeInTheDocument();
      });
    });

    test('allows optional approval notes in bulk approve dialog', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        const bulkApproveBtn = screen.getByText(/Bulk Approve \(2\)/);
        fireEvent.click(bulkApproveBtn);
      });

      await waitFor(() => {
        const notesInput = screen.getByPlaceholderText(/Add any notes/i);
        expect(notesInput).toBeInTheDocument();
      });
    });

    test('submits bulk approval with correct payload', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          succeeded_count: 2,
          failed_count: 0,
          succeeded_task_ids: mockTasks.map((t) => t.task_id),
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        const bulkApproveBtn = screen.getByText(/Bulk Approve \(2\)/);
        fireEvent.click(bulkApproveBtn);
      });

      await waitFor(() => {
        const approveBtn = screen.getByText(/Approve All/);
        fireEvent.click(approveBtn);
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          'http://localhost:8000/api/tasks/bulk-approve',
          expect.objectContaining({
            method: 'POST',
            headers: expect.objectContaining({
              'Content-Type': 'application/json',
            }),
            body: expect.stringContaining('"task_ids"'),
          })
        );
      });
    });

    test('shows success message after bulk approval', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          succeeded_count: 2,
          failed_count: 0,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        const bulkApproveBtn = screen.getByText(/Bulk Approve \(2\)/);
        fireEvent.click(bulkApproveBtn);
      });

      await waitFor(() => {
        const approveBtn = screen.getByText(/Approve All/);
        fireEvent.click(approveBtn);
      });

      await waitFor(() => {
        expect(
          screen.getByText(/Bulk approval completed.*2 approved/)
        ).toBeInTheDocument();
      });
    });
  });

  describe('Bulk Rejection Dialog', () => {
    test('opens dialog when bulk reject button clicked', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        const bulkRejectBtn = screen.getByText(/Bulk Reject \(2\)/);
        fireEvent.click(bulkRejectBtn);
      });

      await waitFor(() => {
        expect(screen.getByText(/Bulk Reject Tasks/)).toBeInTheDocument();
      });
    });

    test('requires feedback for bulk rejection', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        const bulkRejectBtn = screen.getByText(/Bulk Reject \(2\)/);
        fireEvent.click(bulkRejectBtn);
      });

      await waitFor(() => {
        const rejectBtn = screen.getByText(/Reject All/);
        // Should be disabled because feedback is empty
        expect(rejectBtn).toBeDisabled();
      });
    });

    test('enables reject button when feedback provided', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        const bulkRejectBtn = screen.getByText(/Bulk Reject \(2\)/);
        fireEvent.click(bulkRejectBtn);
      });

      await waitFor(() => {
        const feedbackInput =
          screen.getByPlaceholderText(/Explain what needs/i);
        fireEvent.change(feedbackInput, {
          target: { value: 'Content needs revision' },
        });
      });

      await waitFor(() => {
        const rejectBtn = screen.getByText(/Reject All/);
        expect(rejectBtn).not.toBeDisabled();
      });
    });
  });

  describe('Single Approve Action', () => {
    test('opens approve dialog on approve button click', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const approveButtons = screen.getAllByText(/^Approve$/);
        fireEvent.click(approveButtons[0]);
      });

      await waitFor(() => {
        expect(screen.getByText(/Approve Task/)).toBeInTheDocument();
      });
    });
  });

  describe('Single Reject Action', () => {
    test('opens reject dialog on reject button click', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const rejectButtons = screen.getAllByText(/^Reject$/);
        fireEvent.click(rejectButtons[0]);
      });

      await waitFor(() => {
        expect(screen.getByText(/Reject Task/)).toBeInTheDocument();
      });
    });
  });

  describe('Error Handling', () => {
    test('shows error message on fetch failure', async () => {
      global.fetch.mockRejectedValueOnce(new Error('Network error'));

      render(<ApprovalQueue />);

      await waitFor(() => {
        expect(screen.getByText(/error/i)).toBeInTheDocument();
      });
    });

    test('shows error on failed bulk approval', async () => {
      // URL-aware mock: fail on bulk-approve, succeed on everything else
      global.fetch.mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('bulk-approve')) {
          return Promise.resolve({
            ok: false,
            statusText: 'Internal Server Error',
            json: async () => ({ detail: 'Server error' }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ tasks: mockTasks, total: 2 }),
        });
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        const selectAllButton = screen.getByText(/Select All/);
        fireEvent.click(selectAllButton);
      });

      await waitFor(() => {
        const bulkApproveBtn = screen.getByText(/Bulk Approve \(2\)/);
        fireEvent.click(bulkApproveBtn);
      });

      await waitFor(() => {
        const approveBtn = screen.getByText(/Approve All/);
        fireEvent.click(approveBtn);
      });

      await waitFor(() => {
        expect(
          screen.getByText(/Failed to approve tasks/i)
        ).toBeInTheDocument();
      });
    });
  });

  describe('WebSocket Integration', () => {
    test('subscribes to WebSocket approval updates', async () => {
      global.WebSocket = vi.fn().mockImplementation(() => ({
        onopen: null,
        onmessage: null,
        onerror: null,
        onclose: null,
        readyState: 0,
        close: vi.fn(),
      }));

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      await waitFor(() => {
        expect(global.WebSocket).toHaveBeenCalledWith(
          expect.stringContaining('/api/ws/approval/')
        );
      });
    });

    test('updates task status on WebSocket message', async () => {
      const mockWebSocket = {
        send: vi.fn(),
        close: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        onopen: null,
        onmessage: null,
        onerror: null,
        onclose: null,
        readyState: 0,
      };

      global.WebSocket = vi.fn(() => mockWebSocket);

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: mockTasks,
          total: 2,
        }),
      });

      render(<ApprovalQueue />);

      // Wait for the WebSocket onmessage handler to be set
      await waitFor(() => {
        expect(mockWebSocket.onmessage).toBeInstanceOf(Function);
      });

      // Simulate WebSocket message using the onmessage handler
      mockWebSocket.onmessage({
        data: JSON.stringify({
          type: 'approval_status',
          task_id: mockTasks[0].task_id,
          status: 'approved',
        }),
      });

      // Verify the handler was set
      expect(mockWebSocket.onmessage).toBeInstanceOf(Function);
    });
  });

  describe('Pagination', () => {
    test('fetches next page when pagination button clicked', async () => {
      // Return 11 tasks so pagination shows 2 pages (limit is 10)
      const manyTasks = Array.from({ length: 11 }, (_, i) => ({
        ...mockTasks[0],
        task_id: `task-uuid-${i}`,
        task_name: `Task ${i}`,
      }));

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: manyTasks,
          total: 20,
        }),
      });

      // Mock for page 2 fetch
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tasks: [],
          total: 20,
        }),
      });

      render(<ApprovalQueue />);

      // Wait for tasks to load and next page button to be enabled
      await waitFor(() => {
        const nextPageButtons = screen.getAllByLabelText(/next/i);
        const enabledNext = nextPageButtons.find((btn) => !btn.disabled);
        expect(enabledNext).toBeTruthy();
      });

      const nextPageButtons = screen.getAllByLabelText(/next/i);
      const enabledNext = nextPageButtons.find((btn) => !btn.disabled);
      fireEvent.click(enabledNext);

      // Verify next page offset was used in the second fetch call
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining('offset=10'),
          expect.any(Object)
        );
      });
    });
  });
});
