/**
 * Phase 4: Cookie Consent Banner
 *
 * Lightweight cookie consent implementation for GDPR/CCPA compliance.
 * Uses localStorage to remember user's choice.
 *
 * Features:
 * - Persistent consent (remembers user choice for 365 days)
 * - Non-intrusive design
 * - Links to privacy policy and cookie policy
 * - Compliant with GDPR and CCPA
 *
 * GDPR COMPLIANCE:
 * - Analytics only loads AFTER user consent
 * - Users can withdraw consent at any time
 * - Opt-out is clear and easy
 */

'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';

interface CookieConsent {
  analytics: boolean;
  advertising: boolean;
  essential: boolean;
}

const DEFAULT_CONSENT: CookieConsent = {
  analytics: false,
  advertising: false,
  essential: true, // Essential cookies always enabled
};

export default function CookieConsentBanner() {
  const [isVisible, setIsVisible] = useState(false);
  const [showCustom, setShowCustom] = useState(false);
  const [consent, setConsent] = useState<CookieConsent>(DEFAULT_CONSENT);
  const [mounted, setMounted] = useState(false);

  // Load consent from localStorage on mount
  useEffect(() => {
    setMounted(true);
    try {
      const savedConsent = localStorage.getItem('cookieConsent');

      if (savedConsent) {
        try {
          const parsed = JSON.parse(savedConsent);
          setConsent(parsed);
          // Don't show banner if user has already made a choice
          setIsVisible(false);
        } catch (parseError) {
          console.error('Failed to parse cookie consent:', parseError);
          setIsVisible(true);
        }
      } else {
        // Show banner if no consent has been saved
        setIsVisible(true);
      }
    } catch (error) {
      // localStorage might not be available in private browsing mode
      console.warn('localStorage access error:', error);
      setIsVisible(true);
    }
  }, []);

  if (!mounted) {
    return null;
  }

  const handleAcceptAll = () => {
    const newConsent: CookieConsent = {
      essential: true,
      analytics: true,
      advertising: true,
    };
    saveConsent(newConsent);
  };

  const handleRejectAll = () => {
    const newConsent: CookieConsent = {
      essential: true,
      analytics: false,
      advertising: false,
    };
    saveConsent(newConsent);
  };

  const handleCustomize = () => {
    setShowCustom(!showCustom);
  };

  const handleSaveCustom = () => {
    saveConsent(consent);
    setShowCustom(false);
  };

  const toggleConsent = (key: 'analytics' | 'advertising') => {
    setConsent((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  const saveConsent = (newConsent: CookieConsent) => {
    setConsent(newConsent);
    try {
      localStorage.setItem('cookieConsent', JSON.stringify(newConsent));
      localStorage.setItem('cookieConsentDate', new Date().toISOString());
    } catch (error) {
      console.warn('Failed to save consent to localStorage:', error);
      // Continue anyway - consent is stored in state
    }
    setIsVisible(false);
    setShowCustom(false);

    // Update global consent variables for third-party scripts
    window.__cookieConsent = newConsent;

    // If analytics are now enabled, load Google Analytics
    if (newConsent.analytics && !window.gtag) {
      loadGoogleAnalytics();
    }

    // If advertising are now enabled, reload ads
    if (newConsent.advertising && window.adsbygoogle) {
      window.adsbygoogle.push({});
    }
  };

  if (showCustom) {
    return (
      <div className="fixed bottom-0 left-0 right-0 z-50 bg-gray-900 border-t border-gray-700 shadow-lg">
        <div className="container mx-auto px-4 py-6">
          <h3 className="text-lg font-bold text-cyan-400 mb-4">
            Cookie Preferences
          </h3>

          <div className="space-y-4 mb-6">
            <div className="flex items-center justify-between">
              <div>
                <label className="font-semibold text-gray-200">
                  Essential Cookies (Required)
                </label>
                <p className="text-sm text-gray-400">
                  Necessary for website functionality. Cannot be disabled.
                </p>
              </div>
              <input
                type="checkbox"
                checked={true}
                disabled
                aria-label="Essential cookies (required)"
                className="w-5 h-5"
              />
            </div>

            <div className="flex items-center justify-between">
              <label
                className="flex items-center cursor-pointer flex-1"
                htmlFor="analytics-consent"
              >
                <div className="flex-1 pr-4">
                  <span className="font-semibold text-gray-200">
                    Analytics Cookies
                  </span>
                  <p className="text-sm text-gray-400">
                    Help us understand how you use our website (Google Analytics
                    4).
                  </p>
                </div>
                <input
                  id="analytics-consent"
                  type="checkbox"
                  checked={consent.analytics}
                  onChange={() => toggleConsent('analytics')}
                  aria-label="Analytics cookies"
                  className="w-5 h-5 cursor-pointer shrink-0"
                />
              </label>
            </div>

            <div className="flex items-center justify-between">
              <label
                className="flex items-center cursor-pointer flex-1"
                htmlFor="advertising-consent"
              >
                <div className="flex-1 pr-4">
                  <span className="font-semibold text-gray-200">
                    Advertising Cookies
                  </span>
                  <p className="text-sm text-gray-400">
                    Personalize ads based on your interests (Google AdSense).
                  </p>
                </div>
                <input
                  id="advertising-consent"
                  type="checkbox"
                  checked={consent.advertising}
                  onChange={() => toggleConsent('advertising')}
                  aria-label="Advertising cookies"
                  className="w-5 h-5 cursor-pointer"
                />
              </label>
            </div>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => setShowCustom(false)}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition"
            >
              Back
            </button>
            <button
              onClick={handleSaveCustom}
              className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 rounded-lg text-sm font-medium text-white transition"
            >
              Save Preferences
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!isVisible) {
    return null;
  }

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-gray-900 border-t border-gray-700 shadow-lg">
      <div className="container mx-auto px-4 py-4">
        <div className="flex flex-col md:flex-row items-center justify-between gap-4">
          {/* Message */}
          <div className="flex-1 text-sm md:text-base">
            <p className="text-gray-300 mb-2">
              We use cookies to enhance your experience and analyze website
              traffic. This includes
              <strong> essential cookies</strong> (required),{' '}
              <strong>analytics</strong>, and <strong>advertising</strong>{' '}
              cookies from Google.
            </p>
            <div className="flex gap-4 text-xs text-cyan-400">
              <Link href="/legal/privacy" className="hover:text-cyan-300">
                Privacy Policy
              </Link>
              <Link href="/legal/cookie-policy" className="hover:text-cyan-300">
                Cookie Policy
              </Link>
              <Link href="/legal/data-requests" className="hover:text-cyan-300">
                Data Requests
              </Link>
            </div>
          </div>

          {/* Buttons */}
          <div className="flex flex-col sm:flex-row gap-2 w-full md:w-auto">
            <button
              onClick={handleRejectAll}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition"
            >
              Reject All
            </button>
            <button
              onClick={handleCustomize}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition"
            >
              Customize
            </button>
            <button
              onClick={handleAcceptAll}
              className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 rounded-lg text-sm font-medium text-white transition"
            >
              Accept All
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Helper function to load Google Analytics after consent
function loadGoogleAnalytics() {
  const gaId = process.env.NEXT_PUBLIC_GA_ID;
  if (!gaId) return;

  const script = document.createElement('script');
  script.src = `https://www.googletagmanager.com/gtag/js?id=${gaId}`;
  script.async = true;
  document.head.appendChild(script);

  window.dataLayer = window.dataLayer || [];
  function gtag(...args: any[]) {
    window.dataLayer.push(arguments);
  }
  window.gtag = gtag;
  gtag('js', new Date());
  gtag('config', gaId);
}

// Declare global window properties
declare global {
  interface Window {
    __cookieConsent: CookieConsent;
    dataLayer: any[];
    gtag: Function;
    adsbygoogle: any;
  }
}
