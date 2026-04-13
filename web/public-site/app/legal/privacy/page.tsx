import type { Metadata } from 'next';
import { FAQSchema } from '../../../components/StructuredData';
import { SITE_NAME, COMPANY_NAME, SUPPORT_EMAIL, PRIVACY_EMAIL } from '@/lib/site.config';

export const metadata: Metadata = {
  title: `Privacy Policy - ${SITE_NAME}`,
  description: `Privacy Policy for ${SITE_NAME}`,
};

export default function PrivacyPolicy() {
  const lastUpdated = new Date('2026-04-13').toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  // FAQ data for schema markup
  const faqs = [
    {
      question: 'How long do you keep my data?',
      answer:
        'ViewTracker page views are kept indefinitely (no IP stored). Sentry error data: 90 days. Server logs: 90 days. If you consent to Google Analytics: 14 months. If you consent to AdSense: up to 30 months.',
    },
    {
      question: 'What third parties have access to my data?',
      answer:
        'Always active: Vercel (Hosting), Sentry (Error Monitoring), GitHub (Giscus Comments). Consent-gated: Google (Analytics & AdSense, only if you opt in). Each has their own privacy policies.',
    },
    {
      question: 'How do I download my data?',
      answer:
        'Visit our Data Requests page at /legal/data-requests to submit a data portability request. We will provide your data in a machine-readable format within 30 days.',
    },
    {
      question: 'Can I delete my data?',
      answer:
        'Yes, you have the right to erasure under GDPR. Submit a deletion request via our Data Requests page, and we will delete your personal data within 30 days.',
    },
    {
      question: 'Where is my data processed?',
      answer:
        'Your data may be transferred to and processed in the United States by our service providers. We use Standard Contractual Clauses to ensure adequate protection.',
    },
    {
      question: 'How do I contact you about privacy?',
      answer:
        `Email us at ${PRIVACY_EMAIL} with any privacy questions. We aim to respond within 30 days per GDPR requirements.`,
    },
  ];

  return (
    <>
      <FAQSchema faqs={faqs} />
      <div className="prose prose-invert max-w-none">
        <h1 className="text-4xl font-bold text-cyan-400 mb-4">
          Privacy Policy
        </h1>

        <p className="text-gray-400 mb-8">
          <strong>Last Updated:</strong> {lastUpdated}
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          1. Introduction
        </h2>
        <p>
          {COMPANY_NAME} (&quot;we,&quot; &quot;us,&quot; &quot;our&quot;) respects
          your privacy. This policy explains what data we collect when you visit
          gladlabs.io, how we use it, and your rights regarding that data. We
          keep it straightforward because privacy policies shouldn&apos;t
          require a law degree to understand.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          2. Legal Basis for Processing (GDPR)
        </h2>
        <p>
          Under the GDPR, we process your personal data based on the following
          legal bases:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Consent (Article 6(1)(a)):</strong> Google Analytics and
            AdSense are only loaded after you explicitly opt in via our cookie
            banner
          </li>
          <li>
            <strong>Contract Performance (Article 6(1)(b)):</strong> Essential
            cookies and website functionality necessary to serve content
          </li>
          <li>
            <strong>Legal Obligation (Article 6(1)(c)):</strong> Security logs,
            fraud prevention, and legal compliance
          </li>
          <li>
            <strong>Legitimate Interest (Article 6(1)(f)):</strong> First-party
            analytics (ViewTracker), error monitoring (Sentry), and site
            optimization
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          3. Information We Collect
        </h2>
        <p>We collect minimal data. Here&apos;s exactly what and why:</p>

        <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
          3.1 Always-Active Data Collection
        </h3>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>ViewTracker (First-Party Analytics):</strong> Our own
            lightweight analytics system records page path, post slug, referrer
            URL, and user-agent string for each page view. No IP addresses are
            stored. This data stays in our database and is never shared with
            third parties. Legal basis: Legitimate Interest (Article 6(1)(f)).
          </li>
          <li>
            <strong>Sentry (Error Monitoring):</strong> Sentry captures error
            data including IP addresses, browser information, and stack traces
            when something breaks. This helps us fix bugs fast. Legal basis:
            Legitimate Interest (Article 6(1)(f)).
          </li>
          <li>
            <strong>Server Logs:</strong> Our hosting provider (Vercel)
            automatically logs IP addresses, browser type, and pages visited for
            security and operational purposes.
          </li>
        </ul>

        <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
          3.2 Consent-Gated Data Collection
        </h3>
        <p>
          The following services are only activated if you explicitly consent
          via our cookie banner:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Google Analytics 4:</strong> If you consent to analytics
            cookies, GA4 collects usage data including pages visited, time
            spent, and interactions. If you reject analytics, the GA script is
            never loaded.
          </li>
          <li>
            <strong>Google AdSense:</strong> If you consent to advertising
            cookies, AdSense may serve ads and set cookies for personalization.
            If you reject advertising, the AdSense script is never loaded.
          </li>
        </ul>

        <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
          3.3 Third-Party Services
        </h3>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Giscus (Comments):</strong> Our blog uses Giscus, a
            commenting system powered by GitHub Discussions. When you comment,
            you authenticate via GitHub. Your GitHub username, avatar, and
            comment content are stored on GitHub&apos;s servers. Giscus does not
            use cookies or track you beyond the comment interaction.
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          4. How We Use Your Information
        </h2>
        <p>We use collected data to:</p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            Understand which content performs well (first-party analytics)
          </li>
          <li>Fix errors and improve site reliability (Sentry)</li>
          <li>Ensure security and prevent abuse (server logs)</li>
          <li>Comply with legal obligations</li>
        </ul>
        <p>
          Google Analytics and AdSense are only active if you consent. If you
          reject those categories, we collect zero third-party tracking data.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          5. Information Sharing & Disclosure
        </h2>
        <p>
          We do <strong>NOT</strong> sell, trade, or rent your personal
          information. Period. We share data only with:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Service Providers:</strong> Vercel (hosting), Sentry (error
            monitoring), GitHub (comments) — only the data necessary for them to
            provide their services.
          </li>
          <li>
            <strong>Google (consent-gated):</strong> If you opt in to analytics
            and/or advertising, Google receives interaction data per their
            privacy policy.
          </li>
          <li>
            <strong>Legal Compliance:</strong> If required by law or to prevent
            fraud.
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          6. Cookies
        </h2>
        <p>
          We use minimal cookies. Essential cookies are required for the site to
          function. We do not use third-party advertising or tracking cookies.
          See our{' '}
          <a
            href="/legal/cookie-policy"
            className="text-cyan-400 hover:text-cyan-300"
          >
            Cookie Policy
          </a>{' '}
          for full details.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          7. Your Privacy Rights
        </h2>
        <p>You have the right to:</p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Access:</strong> Request a copy of the data we hold about
            you
          </li>
          <li>
            <strong>Deletion:</strong> Request that we delete your data
          </li>
          <li>
            <strong>Portability:</strong> Receive your data in a portable,
            machine-readable format
          </li>
          <li>
            <strong>Rectification:</strong> Correct inaccurate data
          </li>
          <li>
            <strong>Restriction:</strong> Limit how we process your data
          </li>
          <li>
            <strong>Objection:</strong> Object to processing based on legitimate
            interest
          </li>
          <li>
            <strong>Withdraw Consent:</strong> Where consent is the legal basis,
            withdraw it at any time
          </li>
        </ul>
        <p>
          Exercise these rights via our{' '}
          <a
            href="/legal/data-requests"
            className="text-cyan-400 hover:text-cyan-300"
          >
            Data Request page
          </a>{' '}
          or by emailing {PRIVACY_EMAIL}.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          8. Data Security
        </h2>
        <p>
          We use appropriate technical and organizational measures to protect
          your data. Our infrastructure runs on Vercel and Cloudflare with HTTPS
          everywhere. That said, no system is 100% secure — we&apos;re honest
          about that.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          9. Data Retention
        </h2>
        <p>We keep data only as long as necessary:</p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>ViewTracker Data:</strong> Page view analytics (path, slug,
            referrer, user-agent) are retained indefinitely in aggregated form.
            No IP addresses are stored.
          </li>
          <li>
            <strong>Google Analytics Data:</strong> If consented, retained for
            up to 14 months by Google.
          </li>
          <li>
            <strong>Google AdSense Data:</strong> If consented, advertising
            cookies retained for up to 30 months.
          </li>
          <li>
            <strong>Sentry Error Data:</strong> Error reports are retained for
            90 days.
          </li>
          <li>
            <strong>Server Logs:</strong> IP addresses and access logs are
            retained for 90 days.
          </li>
          <li>
            <strong>Cookie Preferences:</strong> Stored in your browser until
            you clear them.
          </li>
          <li>
            <strong>Purchase Data:</strong> Transaction records, billing details,
            and purchase history are retained by Lemon Squeezy as merchant of
            record in accordance with their retention policy and applicable tax
            and accounting requirements.
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          10. Data Processors & Third Parties
        </h2>
        <p>The following third parties may process your data:</p>
        <div className="overflow-x-auto my-4">
          <table className="w-full text-sm border border-gray-600">
            <thead>
              <tr className="bg-gray-800">
                <th className="px-3 py-2 border border-gray-600">Company</th>
                <th className="px-3 py-2 border border-gray-600">Service</th>
                <th className="px-3 py-2 border border-gray-600">
                  Data Processed
                </th>
                <th className="px-3 py-2 border border-gray-600">
                  Privacy Policy
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="px-3 py-2 border border-gray-600">Google LLC</td>
                <td className="px-3 py-2 border border-gray-600">
                  Analytics & AdSense (consent-gated)
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  Pages visited, interactions, ad personalization
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  <a
                    href="https://policies.google.com/privacy"
                    className="text-cyan-400 hover:text-cyan-300"
                  >
                    View Policy
                  </a>
                </td>
              </tr>
              <tr className="bg-gray-900">
                <td className="px-3 py-2 border border-gray-600">Vercel Inc</td>
                <td className="px-3 py-2 border border-gray-600">Hosting</td>
                <td className="px-3 py-2 border border-gray-600">
                  Server logs, IP addresses
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  <a
                    href="https://vercel.com/legal/privacy"
                    className="text-cyan-400 hover:text-cyan-300"
                  >
                    View Policy
                  </a>
                </td>
              </tr>
              <tr className="bg-gray-900">
                <td className="px-3 py-2 border border-gray-600">
                  Sentry (Functional Software Inc)
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  Error Monitoring
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  IP, browser info, error data
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  <a
                    href="https://sentry.io/privacy/"
                    className="text-cyan-400 hover:text-cyan-300"
                  >
                    View Policy
                  </a>
                </td>
              </tr>
              <tr>
                <td className="px-3 py-2 border border-gray-600">GitHub Inc</td>
                <td className="px-3 py-2 border border-gray-600">
                  Comments (Giscus)
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  GitHub username, avatar, comments
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  <a
                    href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"
                    className="text-cyan-400 hover:text-cyan-300"
                  >
                    View Policy
                  </a>
                </td>
              </tr>
              <tr className="bg-gray-900">
                <td className="px-3 py-2 border border-gray-600">
                  Lemon Squeezy LLC
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  Payment Processing (Merchant of Record)
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  Name, email, billing address, payment method, purchase history,
                  IP address
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  <a
                    href="https://www.lemonsqueezy.com/privacy"
                    className="text-cyan-400 hover:text-cyan-300"
                  >
                    View Policy
                  </a>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          11. International Data Transfers
        </h2>
        <p>
          Your data may be processed in the United States by our service
          providers (Vercel, Sentry, Lemon Squeezy, and Google if you consent).
          These transfers are protected by Standard Contractual Clauses (SCCs)
          where applicable.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          12. Automated Decision Making
        </h2>
        <p>
          We do not use automated decision-making or profiling that produces
          legal or similarly significant effects about you.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          13. Children&apos;s Privacy
        </h2>
        <p>
          We do not knowingly collect personal information from children under
          13 (or 16 in the EU). If we learn we&apos;ve collected data from a
          child under these ages, we&apos;ll delete it promptly. Parents: if you
          believe your child&apos;s information was collected, contact us at
          {PRIVACY_EMAIL}.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          14. Third-Party Links
        </h2>
        <p>
          Our site links to external websites. We&apos;re not responsible for
          their privacy practices. Check their policies before sharing personal
          information.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          15. Contact Us
        </h2>
        <p>Questions about privacy? Get in touch:</p>
        <div className="bg-gray-800 p-4 rounded-lg mt-4 mb-4">
          <p>
            <strong>{COMPANY_NAME}</strong>
            <br />
            Privacy Email:{' '}
            <a
              href={`mailto:${PRIVACY_EMAIL}`}
              className="text-cyan-400 hover:text-cyan-300"
            >
              {PRIVACY_EMAIL}
            </a>
            <br />
            General Email: {SUPPORT_EMAIL}
            <br />
            Data Requests:{' '}
            <a
              href="/legal/data-requests"
              className="text-cyan-400 hover:text-cyan-300"
            >
              Submit a request
            </a>
          </p>
        </div>
        <p className="text-sm text-gray-400 mt-4">
          <strong>Response Time:</strong> We respond to all privacy inquiries
          within 30 days as required by GDPR. If you&apos;re not satisfied with
          our response, you have the right to lodge a complaint with your local
          data protection authority.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          16. Policy Updates
        </h2>
        <p>
          We may update this policy. Changes get posted here with an updated
          date. Continued use of the site means you accept the current version.
        </p>
      </div>
    </>
  );
}
