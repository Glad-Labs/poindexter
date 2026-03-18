'use client';

import * as Sentry from '@sentry/nextjs';
import { useState, useRef, useEffect, ChangeEvent, FormEvent } from 'react';
import { subscribeToNewsletter } from '../lib/api-fastapi';

/**
 * Newsletter Signup Modal
 * Modal form for email campaign subscriptions
 * Triggered by "Get Updates" button in footer
 */

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

  // Cleanup timeout on unmount or when modal closes
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  // Focus trap, initial focus, and Escape close (issue #762)
  useEffect(() => {
    if (!isOpen || !dialogRef.current) return;

    const dialog = dialogRef.current;
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
        text: '✅ Successfully subscribed! Check your email for updates.',
      });

      // Reset form after 2 seconds and close
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
      console.error('Newsletter signup error:', error);
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
        className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="newsletter-dialog-title"
          className="bg-slate-900 rounded-2xl shadow-2xl border border-slate-700 w-full max-w-md max-h-96 overflow-y-auto"
        >
          {/* Header */}
          <div className="sticky top-0 bg-gradient-to-r from-slate-900 to-slate-800 px-6 py-4 border-b border-slate-700 flex justify-between items-center">
            <h2
              id="newsletter-dialog-title"
              className="text-xl font-bold bg-gradient-to-r from-cyan-400 to-blue-500 bg-clip-text text-transparent"
            >
              Stay Updated
            </h2>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-200 text-2xl leading-none transition-colors"
              aria-label="Close modal"
            >
              ✕
            </button>
          </div>

          {/* Content */}
          <div className="p-6">
            <p className="text-slate-300 text-sm mb-6">
              Get the latest AI insights, technology trends, and automation
              strategies delivered to your inbox.
            </p>

            {message.text && (
              <div
                role={message.type === 'error' ? 'alert' : 'status'}
                aria-live={message.type === 'error' ? 'assertive' : 'polite'}
                className={`mb-4 p-3 rounded-lg text-sm ${
                  message.type === 'success'
                    ? 'bg-green-500/20 text-green-300 border border-green-500/30'
                    : 'bg-red-500/20 text-red-300 border border-red-500/30'
                }`}
              >
                {message.text}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              {/* Email */}
              <div>
                <label
                  htmlFor="newsletter-email"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Email *
                </label>
                <input
                  id="newsletter-email"
                  type="email"
                  name="email"
                  value={formData.email}
                  onChange={handleInputChange}
                  placeholder="you@example.com"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent"
                  required
                />
              </div>

              {/* Name Fields */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label
                    htmlFor="newsletter-first-name"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    First Name
                  </label>
                  <input
                    id="newsletter-first-name"
                    type="text"
                    name="firstName"
                    value={formData.firstName}
                    onChange={handleInputChange}
                    placeholder="John"
                    className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent text-sm"
                  />
                </div>
                <div>
                  <label
                    htmlFor="newsletter-last-name"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Last Name
                  </label>
                  <input
                    id="newsletter-last-name"
                    type="text"
                    name="lastName"
                    value={formData.lastName}
                    onChange={handleInputChange}
                    placeholder="Doe"
                    className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent text-sm"
                  />
                </div>
              </div>

              {/* Company */}
              <div>
                <label
                  htmlFor="newsletter-company"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Company
                </label>
                <input
                  id="newsletter-company"
                  type="text"
                  name="company"
                  value={formData.company}
                  onChange={handleInputChange}
                  placeholder="Acme Corp"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent text-sm"
                />
              </div>

              {/* Interest Categories */}
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Interests (select any)
                </label>
                <div className="grid grid-cols-2 gap-2">
                  {interestOptions.map((category) => (
                    <label
                      key={category}
                      className="flex items-center gap-2 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={formData.interestCategories.includes(category)}
                        onChange={() => handleCategoryToggle(category)}
                        className="w-4 h-4 bg-slate-800 border border-slate-600 rounded cursor-pointer accent-cyan-500"
                      />
                      <span className="text-sm text-slate-300">{category}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Marketing Consent */}
              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  name="marketingConsent"
                  checked={formData.marketingConsent}
                  onChange={handleInputChange}
                  className="w-4 h-4 bg-slate-800 border border-slate-600 rounded mt-1 cursor-pointer accent-cyan-500"
                />
                <span className="text-xs text-slate-400">
                  I agree to receive marketing emails and campaign updates from
                  Glad Labs
                </span>
              </label>

              {/* Submit Button */}
              <button
                type="submit"
                disabled={isLoading}
                className="w-full mt-6 px-4 py-3 bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-semibold rounded-lg hover:shadow-lg hover:shadow-cyan-500/50 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isLoading ? 'Subscribing...' : 'Get Updates'}
              </button>

              <p className="text-xs text-slate-500 text-center mt-3">
                We respect your privacy. Unsubscribe at any time.
              </p>
            </form>
          </div>
        </div>
      </div>
    </>
  );
};

export default NewsletterModal;
