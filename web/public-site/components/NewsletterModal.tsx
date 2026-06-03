'use client';

import * as Sentry from '@sentry/nextjs';
import { useState, useRef, useEffect, ChangeEvent, FormEvent } from 'react';
import { Button, Eyebrow } from '@glad-labs/brand';
import { SITE_NAME } from '@/lib/site.config';

// Subscribe via local Vercel serverless function (no backend dependency)
async function subscribeToNewsletter(data: Record<string, unknown>) {
  const response = await fetch('/api/newsletter/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Subscription failed');
  }
  return response.json();
}

interface NewsletterModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface FormData {
  email: string;
  firstName: string;
  lastName: string;
  company: string;
  interestCategories: string[];
  marketingConsent: boolean;
}

interface Message {
  type: '' | 'success' | 'error';
  text: string;
}

const INPUT_CLASS =
  'gl-focus-ring w-full px-3 py-2 gl-body gl-body--sm gl-body--primary outline-none transition-colors';

const INPUT_STYLE: React.CSSProperties = {
  background: 'var(--gl-surface)',
  border: '1px solid var(--gl-hairline)',
  borderRadius: 0,
  fontFamily: 'var(--gl-font-mono)',
  fontSize: '0.8125rem',
};

const LABEL_CLASS = 'gl-mono gl-mono--upper block mb-1.5';

const NewsletterModal = ({ isOpen, onClose }: NewsletterModalProps) => {
  const [formData, setFormData] = useState<FormData>({
    email: '',
    firstName: '',
    lastName: '',
    company: '',
    interestCategories: [],
    marketingConsent: false,
  });

  const [isLoading, setIsLoading] = useState(false);
  const [message, setMessage] = useState<Message>({ type: '', text: '' });
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  // The element that had focus when the modal opened (the "Get updates"
  // trigger in the footer) — focus is returned here on close (issue #978a),
  // mirroring CookieConsentBanner.
  const triggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  // Focus trap, initial focus, Escape close (issue #762), plus background
  // inert-ing and focus restoration on close (issue #978a).
  useEffect(() => {
    if (!isOpen || !dialogRef.current) return;

    const dialog = dialogRef.current;

    // Remember the trigger so focus returns to it when the modal closes.
    triggerRef.current = document.activeElement as HTMLElement | null;

    // Mark everything outside the modal inert + hidden from the a11y tree so
    // screen readers and Tab can't reach background content while it's open.
    const siblings = Array.from(document.body.children).filter(
      (el) => !el.contains(dialog)
    ) as HTMLElement[];
    const restore = siblings.map((el) => ({
      el,
      ariaHidden: el.getAttribute('aria-hidden'),
      inert: el.hasAttribute('inert'),
    }));
    siblings.forEach((el) => {
      el.setAttribute('aria-hidden', 'true');
      el.setAttribute('inert', '');
    });

    const focusable = dialog.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    first?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
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
      // Un-inert the background.
      restore.forEach(({ el, ariaHidden, inert }) => {
        if (ariaHidden === null) el.removeAttribute('aria-hidden');
        else el.setAttribute('aria-hidden', ariaHidden);
        if (!inert) el.removeAttribute('inert');
      });
      // Return focus to whatever opened the modal.
      triggerRef.current?.focus();
    };
  }, [isOpen, onClose]);

  const interestOptions = [
    'AI',
    'Technology',
    'Automation',
    'Business',
    'Hardware',
    'Gaming',
  ];

  const handleInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  const handleCategoryToggle = (category: string) => {
    setFormData((prev) => ({
      ...prev,
      interestCategories: prev.interestCategories.includes(category)
        ? prev.interestCategories.filter((c) => c !== category)
        : [...prev.interestCategories, category],
    }));
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    if (!formData.email) {
      setMessage({ type: 'error', text: 'Email is required' });
      return;
    }

    setIsLoading(true);
    setMessage({ type: '', text: '' });

    try {
      const result = await subscribeToNewsletter({
        email: formData.email,
        first_name: formData.firstName,
        last_name: formData.lastName,
        company: formData.company,
        interest_categories: formData.interestCategories,
        marketing_consent: formData.marketingConsent,
      });

      if (!result.success) {
        throw new Error(result.message || 'Subscription failed');
      }

      setMessage({
        type: 'success',
        text: 'Successfully subscribed. Check your inbox.',
      });

      timeoutRef.current = setTimeout(() => {
        setFormData({
          email: '',
          firstName: '',
          lastName: '',
          company: '',
          interestCategories: [],
          marketingConsent: false,
        });
        onClose();
      }, 2000);
    } catch (error) {
      Sentry.captureException(error);
      setMessage({
        type: 'error',
        text:
          (error as Error).message || 'Failed to subscribe. Please try again.',
      });
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-40"
        onClick={onClose}
        aria-hidden="true"
        style={{
          background: 'rgba(4, 6, 9, 0.72)',
          backdropFilter: 'blur(6px)',
        }}
      />

      {/* Modal — zero-radius E3 surface with cyan left tick */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="newsletter-dialog-title"
          className="gl-tick-left w-full max-w-lg max-h-[85vh] overflow-y-auto"
          style={{
            background: 'var(--gl-surface)',
            border: '1px solid var(--gl-hairline-strong)',
            borderRadius: 0,
          }}
        >
          {/* Header */}
          <div
            className="sticky top-0 flex justify-between items-start px-6 py-5"
            style={{
              background: 'var(--gl-surface)',
              borderBottom: '1px solid var(--gl-hairline)',
            }}
          >
            <div>
              <Eyebrow>GLAD LABS · NEWSLETTER</Eyebrow>
              <h2
                id="newsletter-dialog-title"
                className="gl-h2 mt-1"
                style={{ fontSize: '1.5rem' }}
              >
                Stay in the loop.
              </h2>
            </div>
            <button
              onClick={onClose}
              aria-label="Close modal"
              className="gl-focus-ring gl-mono transition-colors hover:text-[color:var(--gl-cyan)]"
              style={{
                color: 'var(--gl-text-muted)',
                background: 'transparent',
                border: 0,
                fontSize: '1.25rem',
                lineHeight: 1,
                padding: '0.25rem 0.5rem',
                cursor: 'pointer',
              }}
              type="button"
            >
              ✕
            </button>
          </div>

          {/* Content */}
          <div className="p-6">
            <p className="gl-body gl-body--sm mb-6">
              Updates when something new ships — AI, hardware, and the edges
              where they meet. No noise.
            </p>

            {message.text && (
              <div
                role={message.type === 'error' ? 'alert' : 'status'}
                aria-live={message.type === 'error' ? 'assertive' : 'polite'}
                className="gl-mono gl-mono--upper mb-5 px-3 py-2.5 flex items-start gap-2"
                style={{
                  background: 'var(--gl-surface)',
                  borderLeft: `3px solid ${
                    message.type === 'success'
                      ? 'var(--gl-mint)'
                      : 'var(--gl-amber)'
                  }`,
                  color:
                    message.type === 'success'
                      ? 'var(--gl-mint)'
                      : 'var(--gl-amber)',
                  fontSize: '0.75rem',
                }}
              >
                <span aria-hidden>
                  {message.type === 'success' ? '✓' : '⚠'}
                </span>
                <span>{message.text}</span>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-5">
              {/* Email */}
              <div>
                <label htmlFor="newsletter-email" className={LABEL_CLASS}>
                  Email *
                </label>
                <input
                  id="newsletter-email"
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  placeholder="you@example.com"
                  className={INPUT_CLASS}
                  style={INPUT_STYLE}
                  required
                />
              </div>

              {/* Name Fields */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label
                    htmlFor="newsletter-first-name"
                    className={LABEL_CLASS}
                  >
                    First Name
                  </label>
                  <input
                    id="newsletter-first-name"
                    type="text"
                    name="firstName"
                    value={formData.firstName}
                    onChange={handleInputChange}
                    placeholder="Jane"
                    className={INPUT_CLASS}
                    style={INPUT_STYLE}
                  />
                </div>
                <div>
                  <label htmlFor="newsletter-last-name" className={LABEL_CLASS}>
                    Last Name
                  </label>
                  <input
                    id="newsletter-last-name"
                    type="text"
                    name="lastName"
                    value={formData.lastName}
                    onChange={handleInputChange}
                    placeholder="Doe"
                    className={INPUT_CLASS}
                    style={INPUT_STYLE}
                  />
                </div>
              </div>

              {/* Company */}
              <div>
                <label htmlFor="newsletter-company" className={LABEL_CLASS}>
                  Company
                </label>
                <input
                  id="newsletter-company"
                  type="text"
                  name="company"
                  value={formData.company}
                  onChange={handleInputChange}
                  placeholder="Glad Labs"
                  className={INPUT_CLASS}
                  style={INPUT_STYLE}
                />
              </div>

              {/* Interest Categories */}
              <div>
                <span className={LABEL_CLASS}>Interests</span>
                <div className="grid grid-cols-2 gap-2">
                  {interestOptions.map((category) => {
                    const checked =
                      formData.interestCategories.includes(category);
                    return (
                      <label
                        key={category}
                        className="gl-focus-ring flex items-center gap-2 cursor-pointer px-2 py-1.5 transition-colors"
                        style={{
                          border: `1px solid ${
                            checked
                              ? 'var(--gl-cyan-border)'
                              : 'var(--gl-hairline)'
                          }`,
                          background: checked
                            ? 'var(--gl-cyan-bg)'
                            : 'transparent',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => handleCategoryToggle(category)}
                          className="w-4 h-4 cursor-pointer accent-[color:var(--gl-cyan)]"
                          style={{ accentColor: 'var(--gl-cyan)' }}
                        />
                        <span className="gl-mono gl-mono--upper text-xs">
                          {category}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* Marketing Consent */}
              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  name="marketingConsent"
                  checked={formData.marketingConsent}
                  onChange={handleInputChange}
                  className="w-4 h-4 mt-0.5 cursor-pointer"
                  style={{ accentColor: 'var(--gl-cyan)' }}
                />
                <span className="gl-body gl-body--sm opacity-80">
                  I agree to receive marketing emails and campaign updates from{' '}
                  {SITE_NAME}.
                </span>
              </label>

              {/* Submit */}
              <div className="pt-2">
                <Button
                  type="submit"
                  variant="primary"
                  disabled={isLoading}
                  className="w-full"
                >
                  {isLoading ? 'Subscribing…' : 'Get updates →'}
                </Button>
              </div>

              {/* No opacity dimming on this small print — at full --gl-text /
                  --gl-text-muted it clears 4.5:1, but opacity-50/60 dropped it
                  below the AA threshold (#976). */}
              <p className="gl-mono gl-mono--upper text-center mt-3" style={{ fontSize: '0.6875rem' }}>
                We respect your privacy · Unsubscribe any time
              </p>
              <p className="gl-body gl-body--sm text-center mt-1" style={{ fontSize: '0.6875rem' }}>
                Your IP address and user-agent are collected with your
                subscription for security and fraud prevention purposes.
              </p>
            </form>
          </div>
        </div>
      </div>
    </>
  );
};

export default NewsletterModal;
