/**
 * Tests for components/notifications/NotificationCenter.jsx
 *
 * Covers:
 * - Renders connection status indicator
 * - Renders notification history button
 * - History dialog opens on button click
 * - Empty state shown when no notifications
 * - Notification subscription and display
 * - Close dialog button
 * - Clear history button
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

// Mock WebSocketContext
vi.mock('../../../context/WebSocketContext', () => ({
  useWebSocket: vi.fn(() => ({ isConnected: true })),
  useTaskProgress: vi.fn(),
}));

// Mock notificationService — vi.hoisted() required so variables are available in factory
const { mockSubscribe, mockDismiss, mockClearAll } = vi.hoisted(() => ({
  mockSubscribe: vi.fn(),
  mockDismiss: vi.fn(),
  mockClearAll: vi.fn(),
}));

vi.mock('../../../services/notificationService', () => ({
  notificationService: {
    subscribe: mockSubscribe,
    dismiss: mockDismiss,
    clearAll: mockClearAll,
  },
}));

// Import after mocks
import { NotificationCenter } from '../../notifications/NotificationCenter';
import { useWebSocket } from '../../../context/WebSocketContext';

describe('NotificationCenter — base render', () => {
  let capturedCallback;

  beforeEach(() => {
    vi.clearAllMocks();
    capturedCallback = null;
    // subscribe returns an unsubscribe fn; capture the callback for manual invocation
    mockSubscribe.mockImplementation((cb) => {
      capturedCallback = cb;
      return vi.fn(); // unsubscribe
    });
  });

  it('renders connection status as Connected when isConnected=true', () => {
    render(<NotificationCenter />);
    expect(screen.getByText(/Connected/i)).toBeInTheDocument();
  });

  it('renders connection status as Disconnected when isConnected=false', () => {
    vi.mocked(useWebSocket).mockReturnValue({ isConnected: false });
    render(<NotificationCenter />);
    expect(screen.getByText(/Disconnected/i)).toBeInTheDocument();
    vi.mocked(useWebSocket).mockReturnValue({ isConnected: true });
  });

  it('renders a history button with badge', () => {
    render(<NotificationCenter />);
    // IconButton containing HistoryIcon should be present
    // The badge renders a button element
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBeGreaterThan(0);
  });

  it('subscribes to notificationService on mount', () => {
    render(<NotificationCenter />);
    expect(mockSubscribe).toHaveBeenCalledTimes(1);
  });

  it('opens history dialog when history button is clicked', () => {
    render(<NotificationCenter />);
    // The last button in the fixed box is the history icon button
    const buttons = screen.getAllByRole('button');
    const historyBtn = buttons[buttons.length - 1];
    fireEvent.click(historyBtn);
    expect(screen.getByText('Notification History')).toBeInTheDocument();
  });

  it('shows empty state in history dialog when no notifications', () => {
    render(<NotificationCenter />);
    const buttons = screen.getAllByRole('button');
    const historyBtn = buttons[buttons.length - 1];
    fireEvent.click(historyBtn);
    expect(screen.getByText(/No notifications yet/i)).toBeInTheDocument();
  });

  it('closes history dialog when Close button is clicked', async () => {
    render(<NotificationCenter />);
    const buttons = screen.getAllByRole('button');
    const historyBtn = buttons[buttons.length - 1];
    fireEvent.click(historyBtn);
    // Dialog is open
    expect(screen.getByText('Notification History')).toBeInTheDocument();
    // Click the Close button in DialogActions
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    // MUI Dialog uses a transition — wait for DOM update
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('closes history dialog when X icon button is clicked', async () => {
    render(<NotificationCenter />);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    // The aria-label="Close notification history" button
    const closeBtn = screen.getByRole('button', {
      name: /Close notification history/i,
    });
    fireEvent.click(closeBtn);
    // MUI Dialog uses a transition — wait for DOM update
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('Clear History button is disabled when no notifications', () => {
    render(<NotificationCenter />);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    const clearBtn = screen.getByRole('button', { name: /Clear History/i });
    expect(clearBtn).toBeDisabled();
  });

  it('adds notification to history when subscribe callback fires with add action', async () => {
    render(<NotificationCenter />);
    // capturedCallback is the subscriber set in beforeEach mock
    act(() => {
      capturedCallback({
        action: 'add',
        notification: {
          id: 'n1',
          type: 'success',
          title: 'Test Title',
          message: 'Test notification message',
          timestamp: new Date(),
        },
      });
    });

    // Open history dialog
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      // The notification appears in the history dialog — getAllByText handles multiple matches
      const matches = screen.getAllByText('Test notification message');
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  it('calls clearAll and dismisses dialog when Clear History is clicked', async () => {
    render(<NotificationCenter />);
    // Add a notification first
    act(() => {
      capturedCallback({
        action: 'add',
        notification: {
          id: 'n2',
          type: 'info',
          title: '',
          message: 'Another notification',
          timestamp: new Date(),
        },
      });
    });

    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);

    const clearBtn = screen.getByRole('button', { name: /Clear History/i });
    expect(clearBtn).not.toBeDisabled();
    fireEvent.click(clearBtn);

    expect(mockClearAll).toHaveBeenCalledTimes(1);
    // Dialog closes after clearing
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});
