'use client';

import * as Sentry from '@sentry/nextjs';
import { useState, useRef, useEffect, ChangeEvent, FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Button, Eyebrow } from '@glad-labs/brand';

// Only what the signup keeps and something reads: the site's route stores an
// email and a first/last name as a Resend contact. The form used to ask for
// company, interests and a marketing-consent tick as well; nothing downstream
// read them, so they were dropped (data minimisation). Add a field only
// together with the code that stores and uses it.
interface SubscribePayload {
  email: string;
  first_name: string;
  last_name: string;
}

// Subscribe via local Vercel serverless function (no backend dependency)
async function subscribeToNewsletter(data: SubscribePayload) {
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
}

const EMPTY_FORM: FormData = { email: '', firstName: '', lastName: '' };

// Where the small print sends a visitor who wants the details. The fragment is
// the id of the newsletter section in app/legal/privacy/page.tsx; the legal
// page tests render both and fail if they stop matching.
const PRIVACY_POLICY_HREF = '/legal/privacy#newsletter';

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
  const [formData, setFormData] = useState<FormData>(EMPTY_FORM);

  const [isLoading, setIsLoading] = useState(false);
  const [message, setMessage] = useState<Message>({ type: '', text: '' });
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  // The element that had focus when the modal opened (the "Get updates"
  // trigger in the footer) — focus is returned here on close (issue #978a),
  // mirroring CookieConsentBanner.
  const triggerRef = useRef<HTMLElement | null>(null);
  const submitRef = useRef<HTMLButtonElement>(null);
  // Set when a submission starts from the submit button: disabling that
  // button for the request drops focus to <body>, so it is handed back once
  // the button is enabled again.
  const restoreSubmitFocusRef = useRef(false);

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

  // A keyboard user who submitted from the button stays on it: the browser
  // moved focus to <body> when the button was disabled, which drops their
  // place in the dialog. The live region still announces the outcome.
  useEffect(() => {
    if (isLoading || !restoreSubmitFocusRef.current) return;
    restoreSubmitFocusRef.current = false;
    if (document.activeElement === document.body) submitRef.current?.focus();
  }, [isLoading]);

  const handleInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    if (!formData.email) {
      setMessage({ type: 'error', text: 'Email is required' });
      return;
    }

    restoreSubmitFocusRef.current =
      document.activeElement === submitRef.current;
    setIsLoading(true);
    setMessage({ type: '', text: '' });

    try {
      const result = await subscribeToNewsletter({
        email: formData.email,
        first_name: formData.firstName,
        last_name: formData.lastName,
      });

      if (!result.success) {
        throw new Error(result.message || 'Subscription failed');
      }

      setMessage({
        type: 'success',
        text: 'Successfully subscribed. Check your inbox.',
      });

      timeoutRef.current = setTimeout(() => {
        setFormData(EMPTY_FORM);
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

  // Portaled to <body>. The effect above inerts every other child of <body>;
  // rendered in place, inside the footer, it left the footer's links and
  // buttons reachable behind the open dialog. The wrapper keeps the overlay
  // and the dialog in one subtree, so the overlay is not inerted with the
  // page (an inert overlay would stop taking the clicks that close it).
  return createPortal(
    <div>
      {/* Overlay. z-[60], like the cookie preferences dialog, so the modal
          sits above the z-50 header and cookie banner (both inert while it
          is open). At z-40/z-50 the banner, later in the DOM, painted over
          the dialog on a small phone: the submit button sat behind the
          banner's buttons, and a tap on them went through to the form. */}
      <div
        className="fixed inset-0 z-[60]"
        onClick={onClose}
        aria-hidden="true"
        style={{
          background: 'rgba(4, 6, 9, 0.72)',
          backdropFilter: 'blur(6px)',
        }}
      />

      {/* Modal — zero-radius E3 surface with cyan left tick. This centring
          layer covers the viewport, so it lets clicks through
          (pointer-events-none) to the overlay's close handler; only the
          dialog takes them. */}
      <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 pointer-events-none">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="newsletter-dialog-title"
          className="gl-tick-left pointer-events-auto w-full max-w-lg max-h-[85vh] overflow-y-auto"
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

              {/* No consent checkbox: submitting this form IS the consent to
                  these updates, and a separate tick changed nothing. */}

              {/* Submit */}
              <div className="pt-2">
                <Button
                  ref={submitRef}
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
              <p className="gl-mono gl-mono--upper gl-mono--label text-center mt-3">
                We respect your privacy · Unsubscribe any time
              </p>
              {/* Say what is kept, and only that. This line used to claim the
                  visitor's IP and user-agent were stored with the
                  subscription; nothing has stored them since the form began
                  posting through the site's own route (2026-04). The link
                  goes to the policy's newsletter section, which spells out
                  the rest. It opens in a new tab so a visitor who stops to
                  read it does not lose the form. It is the last focusable
                  element in the dialog, so the focus trap wraps to and from
                  it. */}
              <p className="gl-mono gl-mono--label text-center mt-1">
                We use your email and name for these updates and nothing else.{' '}
                <a
                  href={PRIVACY_POLICY_HREF}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="gl-focus-ring gl-mono--accent underline underline-offset-2 hover:opacity-80 transition-opacity"
                >
                  Privacy policy
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              </p>
            </form>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default NewsletterModal;
