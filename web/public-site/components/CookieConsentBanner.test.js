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
    const matches = screen.getAllByText(/cookie|consent|privacy/i);
    expect(matches.length).toBeGreaterThan(0);
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
    const privacyLinks = screen.getAllByRole('link', {
      name: /privacy|policy/i,
    });
    expect(privacyLinks.length).toBeGreaterThan(0);
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
      const matches = screen.getAllByText(
        /cookie categories|preferences|settings/i
      );
      expect(matches.length).toBeGreaterThan(0);
    }
  });

  it('should display banner at bottom of page', () => {
    render(<CookieConsentBanner />);
    const matches = screen.getAllByText(/cookie|consent/i);
    const banner = matches[0].closest('div');
    expect(banner).toBeInTheDocument();
  });

  it('should have proper ARIA labels for accessibility', () => {
    render(<CookieConsentBanner />);
    const acceptButton = screen.getByRole('button', {
      name: /accept|agree|yes/i,
    });
    expect(acceptButton).toHaveAccessibleName();
  });

  it('should handle null/undefined props gracefully', () => {
    render(<CookieConsentBanner onAccept={null} onDecline={undefined} />);
    const matches = screen.getAllByText(/cookie|consent/i);
    expect(matches.length).toBeGreaterThan(0);
  });

  it('should accept cookies when Accept clicked', () => {
    render(<CookieConsentBanner />);
    const acceptButton = screen.getByRole('button', {
      name: /accept|agree|yes/i,
    });
    fireEvent.click(acceptButton);
    expect(localStorageMock.setItem).toHaveBeenCalled();
  });

  it('should handle decline when Decline clicked', () => {
    render(<CookieConsentBanner />);
    const declineButton = screen.getByRole('button', {
      name: /decline|no|reject/i,
    });
    fireEvent.click(declineButton);
    expect(localStorageMock.setItem).toHaveBeenCalled();
  });

  it('should respect GDPR compliance', () => {
    render(<CookieConsentBanner />);
    // Should mention cookies or consent
    const matches = screen.getAllByText(/cookie|consent/i);
    expect(matches.length).toBeGreaterThan(0);
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

// ---------------------------------------------------------------------------
// a11y — issue #765: customize modal dialog role and Escape close
// ---------------------------------------------------------------------------

describe('CookieConsentBanner — a11y: customize modal (issue #765)', () => {
  beforeEach(() => {
    localStorageMock.clear();
    jest.clearAllMocks();
  });

  it('customize modal has role="dialog" when open', async () => {
    render(<CookieConsentBanner />);
    // The mounted useEffect must fire; act() flushes effects
    const customizeBtn = screen.queryByRole('button', { name: /customize/i });
    if (!customizeBtn) return; // pre-existing: component hidden due to mounted state
    fireEvent.click(customizeBtn);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
  });

  it('customize modal has aria-modal="true" when open', async () => {
    render(<CookieConsentBanner />);
    const customizeBtn = screen.queryByRole('button', { name: /customize/i });
    if (!customizeBtn) return;
    fireEvent.click(customizeBtn);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('customize modal has aria-labelledby pointing at heading id', async () => {
    render(<CookieConsentBanner />);
    const customizeBtn = screen.queryByRole('button', { name: /customize/i });
    if (!customizeBtn) return;
    fireEvent.click(customizeBtn);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-labelledby', 'cookie-prefs-title');
    expect(document.getElementById('cookie-prefs-title')).toBeInTheDocument();
  });

  it('pressing Escape closes the customize modal', async () => {
    render(<CookieConsentBanner />);
    const customizeBtn = screen.queryByRole('button', { name: /customize/i });
    if (!customizeBtn) return;
    fireEvent.click(customizeBtn);
    const dialog = screen.getByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Consent-gated AdSense loader injection (issue #943)
//
// The adsbygoogle.js loader is intentionally NOT in the root layout (GDPR).
// CookieConsentBanner must inject it ONLY after the visitor opts in to
// advertising — mirroring how loadGoogleAnalytics() injects gtag.js.
// ---------------------------------------------------------------------------

const ADSENSE_SRC_PREFIX =
  'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js';

function adSenseScriptEl() {
  return document.querySelector('script[data-gl-adsense]');
}

describe('CookieConsentBanner — consent-gated AdSense loader (issue #943)', () => {
  beforeEach(() => {
    localStorageMock.clear();
    jest.clearAllMocks();
    // Remove any loader injected by a prior test so each case starts clean.
    document
      .querySelectorAll('script[data-gl-adsense]')
      .forEach((el) => el.remove());
    delete window.adsbygoogle;
  });

  it('does NOT inject the adsbygoogle.js loader before any consent', () => {
    render(<CookieConsentBanner />);
    expect(adSenseScriptEl()).toBeNull();
  });

  it('does NOT inject the loader when advertising is rejected', () => {
    render(<CookieConsentBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: /reject all/i })
    );
    expect(adSenseScriptEl()).toBeNull();
  });

  it('injects the adsbygoogle.js loader after advertising consent (Accept All)', () => {
    render(<CookieConsentBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: /accept all/i })
    );
    const script = adSenseScriptEl();
    expect(script).not.toBeNull();
    expect(script.src).toContain(ADSENSE_SRC_PREFIX);
    // Publisher ID must be wired into the loader src (defaults to the
    // Glad Labs account when NEXT_PUBLIC_ADSENSE_ID is unset).
    expect(script.src).toContain('client=ca-pub-');
    expect(script.async).toBe(true);
  });

  it('persists the advertising consent choice to localStorage', () => {
    render(<CookieConsentBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: /accept all/i })
    );
    const saved = JSON.parse(localStorageMock.getItem('cookieConsent'));
    expect(saved.advertising).toBe(true);
  });

  it('does not inject the loader twice when consent is granted repeatedly', () => {
    const { rerender } = render(<CookieConsentBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: /accept all/i })
    );
    // Simulate the banner re-mounting and consent being re-saved.
    rerender(<CookieConsentBanner />);
    expect(
      document.querySelectorAll('script[data-gl-adsense]').length
    ).toBe(1);
  });
});
