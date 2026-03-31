import type { Metadata } from 'next';
import { FAQSchema } from '../../../components/StructuredData';

export const metadata: Metadata = {
  title: 'Privacy Policy - Glad Labs',
  description: 'Privacy Policy for Glad Labs',
};

export default function PrivacyPolicy() {
  const lastUpdated = new Date('2026-03-30').toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  // FAQ data for schema markup
  const faqs = [
    {
      question: 'How long do you keep my data?',
      answer:
        'We keep Google Analytics data for 14 months, AdSense data for 30 months, ViewTracker page view data indefinitely (no IP stored), Sentry error data for 90 days, server logs for 90 days, and cookie preferences indefinitely unless you delete them.',
    },
    {
      question: 'What third parties have access to my data?',
      answer:
        'We work with Google LLC (Analytics & AdSense), Vercel (Hosting), Sentry (Error Monitoring), and GitHub (Giscus Comments). Each has their own privacy policies that you can review.',
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
        'Email us at privacy@gladlabs.ai with any privacy questions. We aim to respond within 30 days per GDPR requirements.',
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
          Glad Labs ("we," "us," "our," or "Company") is committed to protecting
          your privacy. This Privacy Policy explains how we collect, use,
          disclose, and safeguard your information when you visit our website.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          2. Legal Basis for Processing (GDPR)
        </h2>
        <p>
          Under the GDPR, we process your personal data based on one or more of
          the following legal bases:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Consent (Article 6(1)(a)):</strong> For analytics and
            advertising cookies (you provide explicit consent via our cookie
            banner)
          </li>
          <li>
            <strong>Contract Performance (Article 6(1)(b)):</strong> For session
            cookies and website functionality necessary to provide our services
          </li>
          <li>
            <strong>Legal Obligation (Article 6(1)(c)):</strong> For security
            logs, fraud prevention, and compliance with applicable laws
          </li>
          <li>
            <strong>Legitimate Interest (Article 6(1)(f)):</strong> For website
            optimization, performance improvement, and user experience
            enhancement (with no consent required for essential functionality)
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          3. Information We Collect
        </h2>
        <p>
          We may collect information about you in a variety of ways. The
          information we may collect on the site includes:
        </p>

        <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
          3.1 Automatic Data Collection
        </h3>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Cookies & Tracking Technologies:</strong> We use cookies,
            web beacons, and similar technologies to track your activity on our
            website and store your preferences.
          </li>
          <li>
            <strong>Google Analytics:</strong> We use Google Analytics to
            understand how visitors interact with our website.
          </li>
          <li>
            <strong>ViewTracker (First-Party Analytics):</strong> We operate our
            own lightweight analytics system that records page path, post slug,
            referrer URL, and user-agent string for each page view. This data is
            stored in our own database and is not shared with third parties.
            Legal basis: Legitimate Interest (Article 6(1)(f)).
          </li>
          <li>
            <strong>Sentry (Error Monitoring):</strong> We use Sentry to detect
            and diagnose errors on our website. Sentry may collect IP addresses,
            browser information, and error stack traces when an error occurs.
            Legal basis: Legitimate Interest (Article 6(1)(f)).
          </li>
          <li>
            <strong>Log Data:</strong> Our servers automatically log IP
            addresses, browser type, operating system, pages visited, and time
            spent on pages.
          </li>
        </ul>

        <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
          3.2 Information from Third Parties
        </h3>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Google AdSense:</strong> We partner with Google AdSense to
            serve advertisements on our website. Google may use cookies to
            personalize ads based on your interests.
          </li>
          <li>
            <strong>Giscus (Comments):</strong> Our blog uses Giscus, a
            commenting system powered by GitHub Discussions. When you post a
            comment, you authenticate via your GitHub account. Your GitHub
            username, avatar, and comment content are stored on GitHub. Giscus
            does not use cookies or track you beyond the comment interaction.
            Review GitHub's privacy policy for details.
          </li>
          <li>
            <strong>Social Media:</strong> If you interact with our content on
            social platforms, those platforms may collect additional
            information.
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          4. How We Use Your Information
        </h2>
        <p>We use the information we collect to:</p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>Personalize and improve your browsing experience</li>
          <li>Serve relevant advertisements through Google AdSense</li>
          <li>
            Analyze website traffic and user behavior (via Google Analytics)
          </li>
          <li>Ensure security and prevent fraudulent activity</li>
          <li>Comply with legal obligations</li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          5. Information Sharing & Disclosure
        </h2>
        <p>
          We do <strong>NOT</strong> sell, trade, or rent your personal
          information to third parties. However, we may share information with:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Google Analytics & AdSense:</strong> Google receives data
            about your interactions on our website. Review Google's privacy
            policy for more details.
          </li>
          <li>
            <strong>Service Providers:</strong> We may share data with vendors
            who help us operate our website (hosting providers, analytics
            services).
          </li>
          <li>
            <strong>Legal Compliance:</strong> We may disclose information if
            required by law or when we believe in good faith that disclosure is
            necessary.
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          6. Cookies & Tracking Technologies
        </h2>
        <p>
          Our website uses cookies to enhance your experience. Most browsers
          allow you to refuse cookies or alert you when cookies are being sent.
          However, blocking cookies may affect website functionality.
        </p>
        <p>
          <strong>Types of cookies we use:</strong>
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Essential Cookies:</strong> Required for website
            functionality
          </li>
          <li>
            <strong>Performance Cookies:</strong> Collect data on how visitors
            use our site
          </li>
          <li>
            <strong>Advertising Cookies:</strong> Used by Google AdSense to
            personalize ads
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          7. Your Privacy Rights
        </h2>
        <p>Depending on your location, you may have the following rights:</p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Right to Access:</strong> Request a copy of the data we hold
            about you
          </li>
          <li>
            <strong>Right to Deletion:</strong> Request that we delete your data
          </li>
          <li>
            <strong>Right to Opt-Out:</strong> Opt-out of certain data
            collection or marketing
          </li>
          <li>
            <strong>Right to Portability:</strong> Receive your data in a
            portable format
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          8. Data Security
        </h2>
        <p>
          We implement appropriate technical and organizational measures to
          protect your information against unauthorized access, alteration,
          disclosure, or destruction. However, no method of transmission over
          the internet is 100% secure.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          9. Third-Party Links
        </h2>
        <p>
          Our website may contain links to third-party websites. We are not
          responsible for the privacy practices of external sites. Please review
          their privacy policies before providing any personal information.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          10. Data Retention
        </h2>
        <p>
          We retain your personal data only for as long as necessary to provide
          our services or comply with legal obligations. Specific retention
          periods:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Google Analytics Data:</strong> Analytics data is retained
            for up to 14 months by Google. Session data expires after 30 days of
            inactivity.
          </li>
          <li>
            <strong>Google AdSense Data:</strong> Advertising cookies are
            retained for up to 30 months, subject to user activity.
          </li>
          <li>
            <strong>ViewTracker Data:</strong> Page view analytics (path, slug,
            referrer, user-agent) are retained indefinitely in aggregated form.
            No IP addresses are stored.
          </li>
          <li>
            <strong>Sentry Error Data:</strong> Error reports are retained for
            90 days.
          </li>
          <li>
            <strong>Server Logs:</strong> IP addresses and server logs are
            retained for 90 days for security purposes.
          </li>
          <li>
            <strong>Cookie Preferences:</strong> Your consent preferences are
            stored indefinitely until you withdraw consent.
          </li>
          <li>
            <strong>Contact Form Submissions:</strong> Messages are retained for
            1 year.
          </li>
        </ul>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          11. Data Processors & Third Parties
        </h2>
        <p>
          We work with the following third parties who may process your personal
          data:
        </p>
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
                <td className="px-3 py-2 border border-gray-600">Analytics</td>
                <td className="px-3 py-2 border border-gray-600">
                  IP, behavior, pages visited
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
                <td className="px-3 py-2 border border-gray-600">Google Inc</td>
                <td className="px-3 py-2 border border-gray-600">
                  AdSense (Ads)
                </td>
                <td className="px-3 py-2 border border-gray-600">
                  IP, interests, cookies
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
              <tr>
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
            </tbody>
          </table>
        </div>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          12. International Data Transfers
        </h2>
        <p>
          Your data may be transferred to and stored in the United States for
          processing by our third-party service providers (Google, Vercel).
          These transfers are protected by:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Standard Contractual Clauses (SCCs):</strong> Google Inc.
            and Vercel have Standard Contractual Clauses in place to ensure
            adequate protection of data transferred outside the EU/EEA.
          </li>
          <li>
            <strong>Legitimate Interest:</strong> We process data
            internationally because it is necessary to provide our website
            services.
          </li>
        </ul>
        <p>
          If you have concerns about international data transfers, you may
          contact us at privacy@gladlabs.ai.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          13. Automated Decision Making
        </h2>
        <p>
          We do not use automated decision-making or profiling that produces
          legal or similarly significant effects about you. However, Google
          AdSense may use interest-based profiling to serve personalized
          advertisements.
        </p>
        <p>
          <strong>Your rights:</strong> You have the right to object to
          profiling, request human review, or restrict automated processing.
          Submit requests to privacy@gladlabs.ai.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          14. Children's Privacy
        </h2>
        <p>
          We do not knowingly collect personal information from children under
          13 (or 16 in the EU). If we become aware that we have collected
          information from a child under these ages, we will promptly delete it.
        </p>
        <p>
          <strong>For parents:</strong> If you believe your child's information
          has been collected, please contact us at privacy@gladlabs.ai.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          15. Contact Us
        </h2>
        <p>
          If you have questions about this Privacy Policy or our privacy
          practices, please contact us:
        </p>
        <div className="bg-gray-800 p-4 rounded-lg mt-4 mb-4">
          <p>
            <strong>Glad Labs, LLC</strong>
            <br />
            Privacy Email:
            <a
              href="mailto:privacy@gladlabs.ai"
              className="text-cyan-400 hover:text-cyan-300 ml-2"
            >
              privacy@gladlabs.ai
            </a>
            <br />
            General Email: hello@gladlabs.io
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
          <strong>Response Time:</strong> We aim to respond to all privacy
          inquiries within 30 days as required by GDPR.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          16. Policy Updates
        </h2>
        <p>
          We may update this Privacy Policy from time to time. Changes will be
          posted on this page with an updated "Last Modified" date. Your
          continued use of our website following the posting of revised Privacy
          Policy means you accept and agree to the changes.
        </p>

        <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
          17. Your GDPR Rights
        </h2>
        <p>
          If you are located in the EU or EEA, you have the following rights
          under GDPR:
        </p>
        <ul className="list-disc list-inside space-y-2 mb-4">
          <li>
            <strong>Right to Access (Article 15):</strong> Request a copy of
            your data
          </li>
          <li>
            <strong>Right to Rectification (Article 16):</strong> Correct
            inaccurate data
          </li>
          <li>
            <strong>Right to Erasure (Article 17):</strong> Request deletion of
            your data
          </li>
          <li>
            <strong>Right to Restrict Processing (Article 18):</strong> Limit
            how we use your data
          </li>
          <li>
            <strong>Right to Data Portability (Article 20):</strong> Export your
            data
          </li>
          <li>
            <strong>Right to Object (Article 21):</strong> Object to certain
            processing
          </li>
          <li>
            <strong>Right to Withdraw Consent:</strong> Withdraw consent at any
            time
          </li>
        </ul>
        <p>
          To exercise any of these rights, visit our{' '}
          <a
            href="/legal/data-requests"
            className="text-cyan-400 hover:text-cyan-300"
          >
            data request page
          </a>{' '}
          or contact privacy@gladlabs.ai.
        </p>
        <p className="mt-4 text-sm text-gray-400">
          If you are not satisfied with our response, you have the right to
          lodge a complaint with your local data protection authority.
        </p>
      </div>
    </>
  );
}
