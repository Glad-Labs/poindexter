'use client';
/**
 * Cookie Consent Banner
 * Lightweight GDPR/CCPA compliant cookie consent implementation
 * Uses localStorage to persist user's choice
 */

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Button, Eyebrow } from '@glad-labs/brand';

export default function CookieConsentBanner() {
  const [isVisible, setIsVisible] = useState(false);
  const [showCustomize, setShowCustomize] = useState(false);
  const [tempConsent, setTempConsent] = useState({
    analytics: false,
    advertising: false,
    essential: true,
  });
  const [consent, setConsent] = useState({
    analytics: false,
    advertising: false,
    essential: true,
  });
  const [mounted, setMounted] = useState(false);
  const dialogRef = useRef(null);
  const customizeTriggerRef = useRef(null);

  useEffect(() => {
    setMounted(true);
    const savedConsent = localStorage.getItem('cookieConsent');

    if (savedConsent) {
      try {
        const parsed = JSON.parse(savedConsent);
        setConsent(parsed);
        setTempConsent(parsed);
        setIsVisible(false);
      } catch (_e) {
        setIsVisible(true);
      }
    } else {
      setIsVisible(true);
    }
  }, []);

  // Focus trap and Escape key handler for the customize modal (issue #765)
  useEffect(() => {
    if (!showCustomize || !dialogRef.current) return;

    const dialog = dialogRef.current;
    const focusable = dialog.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    first?.focus();

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        handleCancelCustomize();
        return;
      }
      if (e.key === 'Tab') {
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
      }
    };

    dialog.addEventListener('keydown', handleKeyDown);
    return () => {
      dialog.removeEventListener('keydown', handleKeyDown);
      customizeTriggerRef.current?.focus();
    };
  }, [showCustomize]);

  if (!mounted) {
    return <></>;
  }

  if (!isVisible && !showCustomize) {
    return null;
  }

  const handleAcceptAll = () => {
    saveConsent({ essential: true, analytics: true, advertising: true });
  };

  const handleRejectAll = () => {
    saveConsent({ essential: true, analytics: false, advertising: false });
  };

  const handleCustomize = () => {
    setTempConsent(consent);
    setShowCustomize(true);
  };

  const handleCancelCustomize = () => {
    setShowCustomize(false);
  };

  const handleSaveCustomize = () => {
    saveConsent(tempConsent);
    setShowCustomize(false);
  };

  const toggleAnalytics = () => {
    setTempConsent({ ...tempConsent, analytics: !tempConsent.analytics });
  };

  const toggleAdvertising = () => {
    setTempConsent({ ...tempConsent, advertising: !tempConsent.advertising });
  };

  const saveConsent = (newConsent) => {
    setConsent(newConsent);
    localStorage.setItem('cookieConsent', JSON.stringify(newConsent));
    localStorage.setItem('cookieConsentDate', new Date().toISOString());
    setIsVisible(false);

    if (typeof window !== 'undefined') {
      window.__cookieConsent = newConsent;

      if (newConsent.analytics) {
        loadGoogleAnalytics();
      }

      if (newConsent.advertising && window.adsbygoogle) {
        try {
          window.adsbygoogle.push({});
        } catch {
          // AdSense script not yet loaded -- will be pushed on next load
        }
      }
    }
  };

  return (
    <>
      {/* Consent banner — E3 surface, hairline top border, mono eyebrow */}
      <div
        role="region"
        aria-label="Cookie consent"
        className="fixed bottom-0 left-0 right-0 z-50"
        style={{
          background: 'var(--gl-base)',
          borderTop: '1px solid var(--gl-hairline-strong)',
          backdropFilter: 'blur(8px)',
        }}
      >
        <div className="container mx-auto px-4 sm:px-6 lg:px-8 py-4 md:py-5">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex-1">
              <Eyebrow>GLAD LABS · COOKIES</Eyebrow>
              <p className="gl-body gl-body--sm mt-2">
                We use cookies to run the site and understand what&apos;s
                working. Essential cookies are required; analytics and
                advertising are opt-in.
              </p>
              <div className="flex gap-4 mt-2">
                <Link
                  href="/legal/privacy"
                  target="_blank"
                  className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80 transition-opacity"
                  style={{ fontSize: '0.6875rem' }}
                >
                  Privacy Policy
                </Link>
                <Link
                  href="/legal/cookie-policy"
                  target="_blank"
                  className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80 transition-opacity"
                  style={{ fontSize: '0.6875rem' }}
                >
                  Cookie Policy
                </Link>
              </div>
            </div>

            <div className="flex flex-col sm:flex-row gap-2 w-full md:w-auto flex-shrink-0">
              <Button
                variant="ghost"
                onClick={handleRejectAll}
                type="button"
                aria-label="Reject all cookies"
              >
                Reject All
              </Button>
              <Button
                variant="secondary"
                ref={customizeTriggerRef}
                onClick={handleCustomize}
                type="button"
                aria-label="Customize cookie preferences"
              >
                Customize
              </Button>
              <Button
                variant="primary"
                onClick={handleAcceptAll}
                type="button"
                aria-label="Accept all cookies"
              >
                Accept All
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Preferences modal — matches NewsletterModal surface */}
      {showCustomize && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4"
          style={{
            background: 'rgba(4, 6, 9, 0.72)',
            backdropFilter: 'blur(6px)',
          }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="cookie-prefs-title"
            className="gl-tick-left w-full max-w-lg max-h-[85vh] overflow-y-auto"
            style={{
              background: 'var(--gl-surface)',
              border: '1px solid var(--gl-hairline-strong)',
              borderRadius: 0,
            }}
          >
            {/* Header */}
            <div
              className="px-6 py-5"
              style={{
                background: 'var(--gl-surface)',
                borderBottom: '1px solid var(--gl-hairline)',
              }}
            >
              <Eyebrow>GLAD LABS · PREFERENCES</Eyebrow>
              <h2
                id="cookie-prefs-title"
                className="gl-h2 mt-1"
                style={{ fontSize: '1.5rem' }}
              >
                Cookie preferences.
              </h2>
              <p className="gl-body gl-body--sm mt-2">
                Customize which cookies we can use.
              </p>
            </div>

            {/* Categories */}
            <div className="px-6 py-5 space-y-3">
              {/* Essential — always-on, amber tick */}
              <div
                className="gl-tick-left gl-tick-left--amber flex items-start gap-3 p-4"
                style={{
                  background: 'transparent',
                  border: '1px solid var(--gl-hairline)',
                }}
              >
                <input
                  type="checkbox"
                  id="essential"
                  checked={true}
                  disabled
                  readOnly
                  className="mt-1 cursor-not-allowed opacity-50"
                  style={{ accentColor: 'var(--gl-cyan)' }}
                />
                <div className="flex-1">
                  <label htmlFor="essential" className="gl-mono gl-mono--upper gl-mono--amber block">
                    Essential Cookies
                  </label>
                  <p className="gl-body gl-body--sm mt-1 opacity-70">
                    Required for site functionality. Cannot be disabled.
                  </p>
                </div>
              </div>

              {/* Analytics */}
              <label
                htmlFor="analytics"
                className="gl-focus-ring flex items-start gap-3 p-4 cursor-pointer transition-colors"
                style={{
                  border: `1px solid ${
                    tempConsent.analytics
                      ? 'var(--gl-cyan-border)'
                      : 'var(--gl-hairline)'
                  }`,
                  background: tempConsent.analytics
                    ? 'var(--gl-cyan-bg)'
                    : 'transparent',
                }}
              >
                <input
                  type="checkbox"
                  id="analytics"
                  checked={tempConsent.analytics}
                  onChange={toggleAnalytics}
                  className="mt-1 w-4 h-4 cursor-pointer"
                  style={{ accentColor: 'var(--gl-cyan)' }}
                />
                <div className="flex-1">
                  <span className="gl-mono gl-mono--upper gl-mono--accent block">
                    Analytics Cookies
                  </span>
                  <p className="gl-body gl-body--sm mt-1 opacity-80">
                    Help us understand how you use the site so we can improve
                    performance and UX.
                  </p>
                </div>
              </label>

              {/* Advertising */}
              <label
                htmlFor="advertising"
                className="gl-focus-ring flex items-start gap-3 p-4 cursor-pointer transition-colors"
                style={{
                  border: `1px solid ${
                    tempConsent.advertising
                      ? 'var(--gl-cyan-border)'
                      : 'var(--gl-hairline)'
                  }`,
                  background: tempConsent.advertising
                    ? 'var(--gl-cyan-bg)'
                    : 'transparent',
                }}
              >
                <input
                  type="checkbox"
                  id="advertising"
                  checked={tempConsent.advertising}
                  onChange={toggleAdvertising}
                  className="mt-1 w-4 h-4 cursor-pointer"
                  style={{ accentColor: 'var(--gl-cyan)' }}
                />
                <div className="flex-1">
                  <span className="gl-mono gl-mono--upper gl-mono--accent block">
                    Advertising Cookies
                  </span>
                  <p className="gl-body gl-body--sm mt-1 opacity-80">
                    Enable personalized ads and marketing based on your
                    interests.
                  </p>
                </div>
              </label>
            </div>

            {/* Footer */}
            <div
              className="flex gap-3 px-6 py-4"
              style={{ borderTop: '1px solid var(--gl-hairline)' }}
            >
              <Button
                variant="ghost"
                onClick={handleCancelCustomize}
                type="button"
                className="flex-1"
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleSaveCustomize}
                type="button"
                className="flex-1"
              >
                Save Preferences
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// Helper function to load Google Analytics after consent
function loadGoogleAnalytics() {
  if (typeof window === 'undefined') return;

  const gaId = process.env.NEXT_PUBLIC_GA_ID || process.env.NEXT_PUBLIC_GA4_ID;
  if (!gaId) {
    return;
  }

  const script = document.createElement('script');
  script.src = `https://www.googletagmanager.com/gtag/js?id=${gaId}`;
  script.async = true;
  script.onload = () => {
    window.dataLayer = window.dataLayer || [];
    function gtag(..._args) {
      window.dataLayer.push(arguments);
    }
    window.gtag = gtag;
    gtag('js', new Date());
    gtag('config', gaId);
  };
  document.head.appendChild(script);
}
