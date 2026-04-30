'use client';

import { Button, Card, Eyebrow } from '@glad-labs/brand';
import { PRIVACY_EMAIL } from '@/lib/site.config';

const RIGHTS = [
  {
    value: 'access',
    glyph: '📋',
    title: 'Right to Access',
    blurb:
      'Request a copy of the personal data we hold about you in a structured, commonly used, machine-readable format.',
  },
  {
    value: 'deletion',
    glyph: '🗑️',
    title: 'Right to Erasure',
    blurb:
      'Request deletion of your personal data, subject to certain legal exceptions (e.g., data needed for legal compliance).',
  },
  {
    value: 'portability',
    glyph: '📤',
    title: 'Right to Portability',
    blurb:
      'Request your personal data in a portable format so you can transfer it to another service provider.',
  },
  {
    value: 'correction',
    glyph: '✏️',
    title: 'Right to Rectification',
    blurb:
      'Request correction of inaccurate or incomplete personal data that we hold about you.',
  },
  {
    value: 'restriction',
    glyph: '🛑',
    title: 'Right to Restrict',
    blurb:
      'Request that we stop processing your personal data while a dispute is resolved or other conditions are met.',
  },
  {
    value: 'objection',
    glyph: '🚫',
    title: 'Right to Object',
    blurb:
      'Object to processing of your data for marketing, profiling, or other purposes.',
  },
] as const;

const DATA_CATEGORIES = [
  { value: 'google-analytics', label: 'Google Analytics data (if consented)' },
  { value: 'advertising', label: 'Advertising data / Google AdSense (if consented)' },
  { value: 'errors', label: 'Error monitoring data (Sentry)' },
  { value: 'cookies', label: 'Cookie preferences' },
  { value: 'logs', label: 'Server logs (IP addresses)' },
  { value: 'comments', label: 'Comments (Giscus/GitHub)' },
  { value: 'all', label: 'All my data' },
];

const FAQS = [
  {
    q: 'How long does it take to process my request?',
    a: 'We aim to process all data requests within 30 days. Some requests may require up to 90 days if we need to verify your identity or if your request is particularly complex.',
  },
  {
    q: 'What happens if I request deletion?',
    a: 'If you request deletion, we will delete your personal data from our systems within 30 days. However, some data cannot be deleted due to legal requirements (e.g., server logs for security). We will inform you of any exceptions.',
  },
  {
    q: 'Can I download my data?',
    a: 'Yes, you can request your data in a portable format (JSON or CSV). We will provide all data we hold about you in a structured, standard format.',
  },
  {
    q: 'Will deletion affect my ability to use the site?',
    a: 'Since our website is primarily public content with minimal personal data collection, deletion will mainly affect analytics and personalization. You can continue to browse the site normally.',
  },
  {
    q: 'What if I disagree with your response?',
    a: 'You have the right to lodge a complaint with your local data protection authority if you believe we have not properly handled your request.',
  },
  {
    q: 'Is my request confidential?',
    a: 'Yes. Your request and personal information are handled confidentially and securely. We do not share your request details with third parties.',
  },
];

const INPUT_STYLE: React.CSSProperties = {
  background: 'var(--gl-surface)',
  border: '1px solid var(--gl-hairline)',
  borderRadius: 0,
  fontFamily: 'var(--gl-font-mono)',
  fontSize: '0.8125rem',
};

const INPUT_CLASS =
  'gl-focus-ring w-full px-3 py-2 gl-body gl-body--sm gl-body--primary outline-none transition-colors';
const LABEL_CLASS = 'gl-mono gl-mono--upper block mb-1.5';

function scrollToFormAndSelect(type: string) {
  document.getElementById('request-form')?.scrollIntoView({ behavior: 'smooth' });
  const select = document.getElementById('request-type') as HTMLSelectElement | null;
  if (select) select.value = type;
}

export default function DataRequests() {
  return (
    <div className="container mx-auto px-4 sm:px-6 lg:px-8 max-w-4xl">
      {/* Header */}
      <Eyebrow>GLAD LABS · LEGAL</Eyebrow>
      <h1
        className="mt-2 font-[family-name:var(--gl-font-display)] font-bold text-white text-4xl md:text-5xl leading-tight tracking-tight"
      >
        Data subject rights.
      </h1>
      <p className="gl-body gl-body--lg mt-4 max-w-2xl">
        Manage your personal data and exercise your GDPR rights. Submit a
        request and we&apos;ll respond within 30 days.
      </p>

      {/* Rights grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-10">
        {RIGHTS.map((r) => (
          <Card key={r.value}>
            <div className="text-3xl mb-3">{r.glyph}</div>
            <Card.Title>{r.title}</Card.Title>
            <Card.Body className="mt-2">{r.blurb}</Card.Body>
            <div className="mt-4">
              <button
                type="button"
                onClick={() => scrollToFormAndSelect(r.value)}
                className="gl-mono gl-mono--accent gl-mono--upper hover:opacity-80 transition-opacity cursor-pointer bg-transparent border-0 p-0"
              >
                Submit {r.title.replace('Right to ', '')} request →
              </button>
            </div>
          </Card>
        ))}
      </div>

      {/* Request form */}
      <div
        id="request-form"
        className="gl-tick-left mt-12 p-6 md:p-8"
        style={{
          background: 'var(--gl-surface)',
          border: '1px solid var(--gl-hairline)',
          borderRadius: 0,
        }}
      >
        <Eyebrow>GLAD LABS · REQUEST</Eyebrow>
        <h2
          id="form-title"
          className="gl-h2 mt-1"
          style={{ fontSize: '1.5rem' }}
        >
          Submit a data request.
        </h2>
        <p className="gl-body gl-body--sm mt-2 mb-6">
          Form posts to <code>{PRIVACY_EMAIL}</code> — we&apos;ll reply within
          30 days.
        </p>

        <form
          action={`mailto:${PRIVACY_EMAIL}`}
          method="POST"
          encType="text/plain"
          className="space-y-5"
          aria-labelledby="form-title"
        >
          <div>
            <label htmlFor="request-type" className={LABEL_CLASS}>
              Type of Request *
            </label>
            <select
              id="request-type"
              name="requestType"
              aria-label="Type of GDPR request (access, deletion, portability, correction, restriction, objection, or other)"
              required
              className={INPUT_CLASS}
              style={INPUT_STYLE}
            >
              <option value="">Select a request type</option>
              <option value="access">Access my data</option>
              <option value="deletion">Delete my data</option>
              <option value="portability">Export my data</option>
              <option value="correction">Correct my data</option>
              <option value="restriction">Restrict processing</option>
              <option value="objection">Object to processing</option>
              <option value="other">Other request</option>
            </select>
          </div>

          <div>
            <label htmlFor="email" className={LABEL_CLASS}>
              Your Email Address *
            </label>
            <input
              type="email"
              id="email"
              name="email"
              aria-label="Your email address for identity verification and response"
              required
              placeholder="you@example.com"
              className={INPUT_CLASS}
              style={INPUT_STYLE}
            />
            <p className="gl-body gl-body--sm opacity-70 mt-1">
              We&apos;ll use this to verify your identity and respond to your
              request.
            </p>
          </div>

          <div>
            <label htmlFor="name" className={LABEL_CLASS}>
              Full Name (Optional)
            </label>
            <input
              type="text"
              id="name"
              name="name"
              aria-label="Your full name (optional)"
              placeholder="Matt Gladding"
              className={INPUT_CLASS}
              style={INPUT_STYLE}
            />
          </div>

          <div>
            <label htmlFor="details" className={LABEL_CLASS}>
              Request Details
            </label>
            <textarea
              id="details"
              name="details"
              aria-label="Additional details about your request"
              rows={5}
              placeholder="If you're requesting deletion, specify what data. If requesting correction, explain what's inaccurate."
              className={INPUT_CLASS}
              style={INPUT_STYLE}
            />
          </div>

          <fieldset>
            <legend className={LABEL_CLASS}>Data Categories Involved</legend>
            <div className="space-y-2">
              {DATA_CATEGORIES.map(({ value, label }) => (
                <label
                  key={value}
                  className="gl-focus-ring flex items-center gap-2 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    name="dataCategories"
                    value={value}
                    className="w-4 h-4 cursor-pointer"
                    style={{ accentColor: 'var(--gl-cyan)' }}
                  />
                  <span className="gl-body gl-body--sm">{label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          {/* Verification callout — amber tick, glyph + label (colorblind-safe) */}
          <div
            className="gl-tick-left gl-tick-left--amber p-4"
            style={{
              background: 'transparent',
              border: '1px solid var(--gl-hairline)',
            }}
          >
            <p className="gl-mono gl-mono--upper gl-mono--amber text-xs mb-2 flex items-start gap-2">
              <span aria-hidden>⚠</span>
              <span>VERIFICATION REQUIRED</span>
            </p>
            <p className="gl-body gl-body--sm mb-3">
              We&apos;ll send a verification link to your email address. You
              must confirm your identity before we process your request.
            </p>
            <label className="gl-focus-ring flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                id="consent"
                name="consent"
                aria-label="I understand my request will be verified via email"
                required
                className="w-4 h-4 cursor-pointer"
                style={{ accentColor: 'var(--gl-cyan)' }}
              />
              <span className="gl-body gl-body--sm">
                I understand my request will be verified via email.
              </span>
            </label>
          </div>

          <div>
            <Button type="submit" variant="primary" className="w-full">
              Submit your data request
            </Button>
          </div>

          <p className="gl-mono gl-mono--upper opacity-50 text-center" style={{ fontSize: '0.6875rem' }}>
            Handled per GDPR · Confirmation within 24 hours
          </p>
        </form>
      </div>

      {/* FAQ */}
      <div className="mt-12">
        <Eyebrow>GLAD LABS · FAQ</Eyebrow>
        <h2 className="gl-h2 mt-1 mb-4">Frequently asked questions.</h2>
        <div className="space-y-2">
          {FAQS.map(({ q, a }, idx) => (
            <details
              key={idx}
              className="group p-4"
              style={{
                background: 'var(--gl-surface)',
                border: '1px solid var(--gl-hairline)',
              }}
            >
              <summary className="flex items-center justify-between cursor-pointer gl-mono gl-mono--upper gl-mono--accent list-none">
                <span>{q}</span>
                <span
                  aria-hidden
                  className="opacity-70 group-open:rotate-180 transition-transform"
                >
                  ▼
                </span>
              </summary>
              <p className="gl-body gl-body--sm mt-3">{a}</p>
            </details>
          ))}
        </div>
      </div>

      {/* Contact */}
      <Card className="mt-12 mb-4">
        <Card.Meta>NEED HELP?</Card.Meta>
        <p className="gl-body mt-3">
          For questions about your data rights or if you need assistance:
        </p>
        <ul className="gl-body gl-body--sm mt-3 space-y-2 list-none">
          <li>
            📧 <strong>Email:</strong>{' '}
            <a
              href={`mailto:${PRIVACY_EMAIL}`}
              className="text-[color:var(--gl-cyan)] hover:underline"
            >
              {PRIVACY_EMAIL}
            </a>
          </li>
          <li>
            📋 <strong>Privacy Policy questions:</strong>{' '}
            <a
              href="/legal/privacy"
              className="text-[color:var(--gl-cyan)] hover:underline"
            >
              Read the Privacy Policy
            </a>
          </li>
          <li>
            🍪 <strong>Cookie questions:</strong>{' '}
            <a
              href="/legal/cookie-policy"
              className="text-[color:var(--gl-cyan)] hover:underline"
            >
              Read the Cookie Policy
            </a>
          </li>
        </ul>
      </Card>
    </div>
  );
}
