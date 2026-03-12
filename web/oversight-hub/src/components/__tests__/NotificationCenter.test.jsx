import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { NotificationCenter } from '../notifications/NotificationCenter';

// vi.hoisted() required when mock variable is referenced in vi.mock() factory
const { mockSubscribe } = vi.hoisted(() => ({
  mockSubscribe: vi.fn(() => vi.fn()),
}));

// Mock notificationService
vi.mock('../../services/notificationService', () => ({
  notificationService: {
    subscribe: mockSubscribe,
  },
}));

// Mock WebSocket context
vi.mock('../../context/WebSocketContext', () => ({
  useWebSocket: () => ({ isConnected: true }),
}));

describe('NotificationCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSubscribe.mockReturnValue(vi.fn()); // fresh unsubscribe fn each test
  });

  it('renders without crashing and subscribes to notification service', () => {
    render(<NotificationCenter />);
    expect(mockSubscribe).toHaveBeenCalledTimes(1);
  });

  it('shows no snackbar when there is no current notification', () => {
    render(<NotificationCenter />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('renders notification when notification service emits an add action', () => {
    let listener;
    mockSubscribe.mockImplementation((fn) => {
      listener = fn;
      return vi.fn();
    });

    render(<NotificationCenter />);

    // NotificationCenter calls timestamp.toLocaleTimeString(), so pass a Date
    const testNotification = {
      id: 'notif-1',
      type: 'success',
      title: 'Task Complete',
      message: 'Blog post generated successfully',
      timestamp: new Date(),
    };

    act(() => {
      listener({ action: 'add', notification: testNotification });
    });

    expect(
      screen.getByText('Blog post generated successfully')
    ).toBeInTheDocument();
  });

  it('shows notification history icon button', () => {
    render(<NotificationCenter />);
    expect(document.querySelector('.MuiIconButton-root')).toBeInTheDocument();
  });

  it('opens history dialog when history button is clicked', () => {
    let listener;
    mockSubscribe.mockImplementation((fn) => {
      listener = fn;
      return vi.fn();
    });

    render(<NotificationCenter />);

    // Add a notification so history has something
    act(() => {
      listener({
        action: 'add',
        notification: {
          id: 'notif-hist-1',
          type: 'info',
          title: 'Info',
          message: 'Something happened',
          timestamp: new Date(),
        },
      });
    });

    // Click the last icon button (History)
    const buttons = document.querySelectorAll('.MuiIconButton-root');
    fireEvent.click(buttons[buttons.length - 1]);

    expect(screen.getByText('Notification History')).toBeInTheDocument();
  });

  it('unsubscribes from notification service on unmount', () => {
    const mockUnsubscribe = vi.fn();
    mockSubscribe.mockReturnValue(mockUnsubscribe);

    const { unmount } = render(<NotificationCenter />);
    unmount();

    expect(mockUnsubscribe).toHaveBeenCalledTimes(1);
  });

  it('clears current notification when remove action is received', () => {
    let listener;
    mockSubscribe.mockImplementation((fn) => {
      listener = fn;
      return vi.fn();
    });

    render(<NotificationCenter />);

    const notification = {
      id: 'notif-remove-1',
      type: 'warning',
      title: 'Warning',
      message: 'About to be removed',
      timestamp: new Date(),
    };

    act(() => {
      listener({ action: 'add', notification });
    });
    expect(screen.getByText('About to be removed')).toBeInTheDocument();

    act(() => {
      listener({ action: 'remove', notification });
    });
    expect(screen.queryByText('About to be removed')).not.toBeInTheDocument();
  });
});
