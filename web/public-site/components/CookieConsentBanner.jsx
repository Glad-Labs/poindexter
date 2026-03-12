'use client';
/**
 * Cookie Consent Banner
 * Lightweight GDPR/CCPA compliant cookie consent implementation
 * Uses localStorage to persist user's choice
 */

import { useEffect, useState } from 'react';
import Link from 'next/link';

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

  // Load consent from localStorage on mount
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
        console.error('Error parsing saved consent');
        setIsVisible(true);
      }
    } else {
      // Show banner if no consent has been saved
      setIsVisible(true);
    }
  }, []);

  if (!mounted) {
    return <></>; // Return empty fragment instead of null to keep hydration consistent
  }

  // Show nothing if user has accepted/rejected and modal is not open
  if (!isVisible && !showCustomize) {
    return null;
  }

  const handleAcceptAll = () => {
    const newConsent = {
      essential: true,
      analytics: true,
      advertising: true,
    };
    saveConsent(newConsent);
  };

  const handleRejectAll = () => {
    const newConsent = {
      essential: true,
      analytics: false,
      advertising: false,
    };
    saveConsent(newConsent);
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
    setTempConsent({
      ...tempConsent,
      analytics: !tempConsent.analytics,
    });
  };

  const toggleAdvertising = () => {
    setTempConsent({
      ...tempConsent,
      advertising: !tempConsent.advertising,
    });
  };

  const saveConsent = (newConsent) => {
    setConsent(newConsent);
    localStorage.setItem('cookieConsent', JSON.stringify(newConsent));
    localStorage.setItem('cookieConsentDate', new Date().toISOString());
    setIsVisible(false);

    // Update global consent variable
    if (typeof window !== 'undefined') {
      window.__cookieConsent = newConsent;

      // If analytics enabled, load Google Analytics
      if (newConsent.analytics) {
        loadGoogleAnalytics();
      }

      // If advertising enabled, reload ads
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
      {/* Cookie Consent Banner */}
      <div className="fixed bottom-0 left-0 right-0 z-50 bg-gray-900 border-t border-gray-700 shadow-lg">
        <div className="container mx-auto px-4 py-4">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            {/* Message */}
            <div className="flex-1 text-sm md:text-base">
              <p className="text-gray-300 mb-2">
                We use cookies to enhance your experience and analyze website
                traffic. This includes <strong>essential cookies</strong>{' '}
                (required), <strong>analytics</strong>, and{' '}
                <strong>advertising</strong> cookies.
              </p>
              <div className="flex gap-4 text-xs text-cyan-400">
                <Link
                  href="/legal/privacy"
                  target="_blank"
                  className="hover:text-cyan-300"
                >
                  Privacy Policy
                </Link>
                <Link
                  href="/legal/cookie-policy"
                  target="_blank"
                  className="hover:text-cyan-300"
                >
                  Cookie Policy
                </Link>
              </div>
            </div>

            {/* Buttons */}
            <div className="flex flex-col sm:flex-row gap-2 w-full md:w-auto flex-shrink-0">
              <button
                onClick={handleRejectAll}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition cursor-pointer whitespace-nowrap"
                type="button"
              >
                Reject All
              </button>
              <button
                onClick={handleCustomize}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition cursor-pointer whitespace-nowrap"
                type="button"
              >
                Customize
              </button>
              <button
                onClick={handleAcceptAll}
                className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 rounded-lg text-sm font-medium text-white transition cursor-pointer whitespace-nowrap"
                type="button"
              >
                Accept All
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Customization Modal */}
      {showCustomize && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50">
          <div className="bg-gray-800 rounded-xl shadow-2xl max-w-md w-full mx-4 border border-gray-700">
            {/* Header */}
            <div className="bg-gradient-to-r from-cyan-600 to-blue-600 px-6 py-4 rounded-t-xl">
              <h2 className="text-xl font-bold text-white">
                Cookie Preferences
              </h2>
              <p className="text-cyan-100 text-sm mt-1">
                Customize which cookies we can use
              </p>
            </div>

            {/* Content */}
            <div className="px-6 py-5 space-y-4">
              {/* Essential Cookies */}
              <div className="flex items-start gap-3 p-3 bg-gray-700/50 rounded-lg border border-gray-600">
                <input
                  type="checkbox"
                  id="essential"
                  checked={true}
                  disabled
                  className="mt-1 cursor-not-allowed opacity-50"
                />
                <div className="flex-1">
                  <label
                    htmlFor="essential"
                    className="font-semibold text-gray-200 block"
                  >
                    Essential Cookies
                  </label>
                  <p className="text-xs text-gray-400 mt-1">
                    Required for site functionality. Cannot be disabled.
                  </p>
                </div>
              </div>

              {/* Analytics Cookies */}
              <div className="flex items-start gap-3 p-3 bg-gray-700/30 rounded-lg border border-gray-600 hover:bg-gray-700/50 transition">
                <input
                  type="checkbox"
                  id="analytics"
                  checked={tempConsent.analytics}
                  onChange={toggleAnalytics}
                  className="mt-1 cursor-pointer w-4 h-4 accent-cyan-500"
                />
                <div className="flex-1">
                  <label
                    htmlFor="analytics"
                    className="font-semibold text-gray-200 block cursor-pointer"
                  >
                    Analytics Cookies
                  </label>
                  <p className="text-xs text-gray-400 mt-1">
                    Help us understand how you use our site to improve
                    performance and user experience.
                  </p>
                </div>
              </div>

              {/* Advertising Cookies */}
              <div className="flex items-start gap-3 p-3 bg-gray-700/30 rounded-lg border border-gray-600 hover:bg-gray-700/50 transition">
                <input
                  type="checkbox"
                  id="advertising"
                  checked={tempConsent.advertising}
                  onChange={toggleAdvertising}
                  className="mt-1 cursor-pointer w-4 h-4 accent-cyan-500"
                />
                <div className="flex-1">
                  <label
                    htmlFor="advertising"
                    className="font-semibold text-gray-200 block cursor-pointer"
                  >
                    Advertising Cookies
                  </label>
                  <p className="text-xs text-gray-400 mt-1">
                    Enable personalized ads and marketing based on your
                    interests.
                  </p>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="flex gap-3 px-6 py-4 bg-gray-700/30 rounded-b-xl border-t border-gray-600">
              <button
                onClick={handleCancelCustomize}
                className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm font-medium transition cursor-pointer"
                type="button"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveCustomize}
                className="flex-1 px-4 py-2 bg-cyan-600 hover:bg-cyan-500 rounded-lg text-sm font-medium text-white transition cursor-pointer"
                type="button"
              >
                Save Preferences
              </button>
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
