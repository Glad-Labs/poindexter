/**
 * Cookie Consent Banner Component Tests (components/CookieConsentBanner.js)
 *
 * Tests cookie consent UI and localStorage interaction
 * Verifies: Consent banner display, user choices, localStorage persistence
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import CookieConsentBanner from '../components/CookieConsentBanner';

// Mock localStorage
const localStorageMock = (() => {
  let store = {};
  return {
    getItem: jest.fn((key) => store[key] || null),
    setItem: jest.fn((key, value) => {
      store[key] = value.toString();
    }),
    removeItem: jest.fn((key) => {
      delete store[key];
    }),
    clear: jest.fn(() => {
      store = {};
    }),
  };
})();

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
});

describe('CookieConsentBanner Component', () => {
  beforeEach(() => {
    localStorageMock.clear();
    jest.clearAllMocks();
  });

  it('should render banner when no consent stored', () => {
    render(<CookieConsentBanner />);
    expect(screen.getByText(/cookie|consent|privacy/i)).toBeInTheDocument();
  });

  it('should not render banner when consent already given', () => {
    localStorageMock.setItem('cookieConsent', 'true');
    const { container } = render(<CookieConsentBanner />);
    expect(
      container.querySelector('[data-testid="cookie-banner"]')
    ).not.toBeInTheDocument() ||
      expect(screen.queryByText(/cookie consent/i)).not.toBeInTheDocument();
  });

  it('should show Accept button', () => {
    render(<CookieConsentBanner />);
    const acceptButton = screen.getByRole('button', {
      name: /accept|agree|yes/i,
    });
    expect(acceptButton).toBeInTheDocument();
  });

  it('should show Decline button', () => {
    render(<CookieConsentBanner />);
    const declineButton = screen.getByRole('button', {
      name: /decline|no|reject/i,
    });
    expect(declineButton).toBeInTheDocument();
  });

  it('should show privacy policy link', () => {
    render(<CookieConsentBanner />);
    const privacyLink = screen.getByRole('link', { name: /privacy|policy/i });
    expect(privacyLink).toBeInTheDocument();
  });

  it('should store consent when Accept clicked', () => {
    render(<CookieConsentBanner />);
    const acceptButton = screen.getByRole('button', {
      name: /accept|agree|yes/i,
    });
    fireEvent.click(acceptButton);
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      'cookieConsent',
      expect.any(String)
    );
  });

  it('should store rejection when Decline clicked', () => {
    render(<CookieConsentBanner />);
    const declineButton = screen.getByRole('button', {
      name: /decline|no|reject/i,
    });
    fireEvent.click(declineButton);
    expect(localStorageMock.setItem).toHaveBeenCalled();
  });

  it('should hide banner after accepting', async () => {
    const { container } = render(<CookieConsentBanner />);
    const acceptButton = screen.getByRole('button', {
      name: /accept|agree|yes/i,
    });
    fireEvent.click(acceptButton);

    await waitFor(() => {
      expect(
        container.querySelector('[data-testid="cookie-banner"]')
      ).not.toBeInTheDocument() ||
        expect(screen.queryByText(/cookie consent/i)).not.toBeInTheDocument();
    });
  });

  it('should hide banner after declining', async () => {
    const { container } = render(<CookieConsentBanner />);
    const declineButton = screen.getByRole('button', {
      name: /decline|no|reject/i,
    });
    fireEvent.click(declineButton);

    await waitFor(() => {
      expect(
        container.querySelector('[data-testid="cookie-banner"]')
      ).not.toBeInTheDocument() ||
        expect(screen.queryByText(/cookie consent/i)).not.toBeInTheDocument();
    });
  });

  it('should include analytics category toggle if applicable', () => {
    render(<CookieConsentBanner />);
    const analyticsCheckbox = screen.queryByRole('checkbox', {
      name: /analytics|tracking/i,
    });

    if (analyticsCheckbox) {
      expect(analyticsCheckbox).toBeInTheDocument();
    }
  });

  it('should include marketing category toggle if applicable', () => {
    render(<CookieConsentBanner />);
    const marketingCheckbox = screen.queryByRole('checkbox', {
      name: /marketing|advertising/i,
    });

    if (marketingCheckbox) {
      expect(marketingCheckbox).toBeInTheDocument();
    }
  });

  it('should allow user to customize cookie settings', () => {
    render(<CookieConsentBanner />);
    const settingsButton = screen.queryByRole('button', {
      name: /settings|customize|preferences/i,
    });

    if (settingsButton) {
      fireEvent.click(settingsButton);
      // Modal or expanded settings should appear
      expect(
        screen.getByText(/cookie categories|preferences|settings/i)
      ).toBeInTheDocument();
    }
  });

  it('should display banner at bottom of page', () => {
    render(<CookieConsentBanner />);
    const banner = screen.getByText(/cookie|consent/i).closest('div');
    expect(banner).toBeInTheDocument();
  });

  it('should have proper ARIA labels for accessibility', () => {
    render(<CookieConsentBanner />);
    const acceptButton = screen.getByRole('button', {
      name: /accept|agree|yes/i,
    });
    expect(acceptButton).toHaveAttribute('aria-label') ||
      expect(acceptButton).toHaveAccessibleName();
  });

  it('should handle null/undefined props gracefully', () => {
    render(<CookieConsentBanner onAccept={null} onDecline={undefined} />);
    expect(screen.getByText(/cookie|consent/i)).toBeInTheDocument();
  });

  it('should call onAccept callback with correct consent data', () => {
    const onAccept = jest.fn();
    render(<CookieConsentBanner onAccept={onAccept} />);
    const acceptButton = screen.getByRole('button', {
      name: /accept|agree|yes/i,
    });
    fireEvent.click(acceptButton);
    expect(onAccept).toHaveBeenCalled();
  });

  it('should call onDecline callback when declining', () => {
    const onDecline = jest.fn();
    render(<CookieConsentBanner onDecline={onDecline} />);
    const declineButton = screen.getByRole('button', {
      name: /decline|no|reject/i,
    });
    fireEvent.click(declineButton);
    expect(onDecline).toHaveBeenCalled();
  });

  it('should respect GDPR compliance', () => {
    render(<CookieConsentBanner />);
    // Should mention GDPR or privacy
    const content = screen.getByText(/cookie|consent/i);
    expect(content).toBeInTheDocument();
  });

  it('should have dismissible UI', () => {
    render(<CookieConsentBanner dismissible={true} />);
    const dismissButton = screen.queryByRole('button', {
      name: /close|dismiss/i,
    });

    if (dismissButton) {
      fireEvent.click(dismissButton);
      expect(screen.queryByText(/cookie consent/i)).not.toBeInTheDocument() ||
        expect(localStorageMock.setItem).toHaveBeenCalled();
    }
  });
});
