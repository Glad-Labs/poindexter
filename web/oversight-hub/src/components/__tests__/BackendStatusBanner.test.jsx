import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Use vi.hoisted so the mock object is available inside the vi.mock factory
const { mockServiceStatus } = vi.hoisted(() => {
  const mockServiceStatus = {
    offline: false,
    subscribe: vi.fn(),
  };
  return { mockServiceStatus };
});

vi.mock('@/lib/serviceStatus', () => ({
  serviceStatus: mockServiceStatus,
}));

import BackendStatusBanner from '../BackendStatusBanner';

describe('BackendStatusBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockServiceStatus.offline = false;
    mockServiceStatus.subscribe.mockReturnValue(vi.fn());
  });

  it('renders nothing when backend is online', () => {
    mockServiceStatus.offline = false;
    const { container } = render(<BackendStatusBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the offline banner when backend is offline at mount', () => {
    mockServiceStatus.offline = true;
    render(<BackendStatusBanner />);
    expect(screen.getByText(/Backend unreachable/i)).toBeInTheDocument();
  });

  it('has role="status" and aria-live="polite" for accessibility', () => {
    mockServiceStatus.offline = true;
    render(<BackendStatusBanner />);
    const banner = screen.getByRole('status');
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveAttribute('aria-live', 'polite');
  });

  it('shows the banner when subscriber notifies offline=true', () => {
    mockServiceStatus.offline = false;

    let capturedListener;
    mockServiceStatus.subscribe.mockImplementation((fn) => {
      capturedListener = fn;
      return vi.fn();
    });

    render(<BackendStatusBanner />);
    expect(screen.queryByRole('status')).toBeNull();

    act(() => {
      capturedListener({ offline: true });
    });

    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/Backend unreachable/i)).toBeInTheDocument();
  });

  it('hides the banner when subscriber notifies offline=false', () => {
    mockServiceStatus.offline = true;

    let capturedListener;
    mockServiceStatus.subscribe.mockImplementation((fn) => {
      capturedListener = fn;
      return vi.fn();
    });

    render(<BackendStatusBanner />);
    expect(screen.getByRole('status')).toBeInTheDocument();

    act(() => {
      capturedListener({ offline: false });
    });

    expect(screen.queryByRole('status')).toBeNull();
  });

  it('calls the unsubscribe function on unmount', () => {
    const mockUnsub = vi.fn();
    mockServiceStatus.subscribe.mockReturnValue(mockUnsub);

    const { unmount } = render(<BackendStatusBanner />);
    unmount();

    expect(mockUnsub).toHaveBeenCalledTimes(1);
  });

  it('subscribes exactly once on mount', () => {
    render(<BackendStatusBanner />);
    expect(mockServiceStatus.subscribe).toHaveBeenCalledTimes(1);
  });
});
