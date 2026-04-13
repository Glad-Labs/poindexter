import type { Metadata } from 'next';
import { SITE_NAME, SITE_URL, COMPANY_NAME, PRIVACY_EMAIL, OWNER_EMAIL } from '@/lib/site.config';

export const metadata: Metadata = {
  title: `Cookie Policy - ${SITE_NAME}`,
  description: `Cookie Policy for ${SITE_NAME}`,
};

export default function CookiePolicy() {
  const lastUpdated = new Date('2026-04-13').toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  return (
    <div className="prose prose-invert max-w-none">
      <h1 className="text-4xl font-bold text-cyan-400 mb-4">Cookie Policy</h1>

      <p className="text-gray-400 mb-8">
        <strong>Last Updated:</strong> {lastUpdated}
      </p>

      <p>
        This is the cookie policy for{' '}
        <a
          href={SITE_URL}
          className="text-cyan-400 hover:text-cyan-300"
        >
          {SITE_URL.replace('https://', '')}
        </a>
        , operated by {COMPANY_NAME}. We try to be straightforward here — no
        walls of legalese, just an honest explanation of what cookies and
        third-party services our site uses and why.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        1. What Are Cookies?
      </h2>
      <p>
        Cookies are small text files stored on your device when you visit a
        website. They help sites remember things (like your preferences) and
        collect usage data. Some are set by us, others by third-party services
        we integrate.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        2. Our Cookie Consent Banner
      </h2>
      <p>
        When you first visit the site, you will see a cookie consent banner at
        the bottom of the page. You have three options:
      </p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          <strong>Accept All:</strong> Enables essential, analytics, and
          advertising cookies/services.
        </li>
        <li>
          <strong>Reject All:</strong> Only essential cookies remain active. No
          analytics or advertising scripts are loaded.
        </li>
        <li>
          <strong>Customize:</strong> Choose exactly which categories to enable
          or disable.
        </li>
      </ul>
      <p>
        Your choice is saved in your browser&apos;s <code>localStorage</code>{' '}
        (specifically the keys <code>cookieConsent</code> and{' '}
        <code>cookieConsentDate</code>). We do not set a cookie to track your
        consent — we use localStorage instead.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        3. What We Actually Use
      </h2>
      <p>
        Here is an honest breakdown of the cookies and third-party services on
        this site:
      </p>

      <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
        3.1 Essential (Always Active)
      </h3>
      <p>These cannot be disabled because the site needs them to function:</p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          <strong>Cookie consent state</strong> — stored in{' '}
          <code>localStorage</code>, not as a cookie. Records whether you
          accepted, rejected, or customized your preferences.
        </li>
        <li>
          <strong>Vercel hosting</strong> — our hosting provider (Vercel) may
          process your IP address for routing, security, and basic
          infrastructure purposes. This is standard for any hosted website.
        </li>
      </ul>

      <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
        3.2 Analytics (Consent Required)
      </h3>
      <p>These are only loaded if you opt in via the consent banner:</p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          <strong>Google Analytics 4 (GA4)</strong> — loaded dynamically after
          consent. Sets cookies like <code>_ga</code> and <code>_ga_*</code> to
          track page views and usage patterns. These cookies last up to 2 years.
          If you reject analytics, the GA script is never loaded and no GA
          cookies are set.
        </li>
      </ul>

      <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
        3.3 Advertising (Consent Required)
      </h3>
      <p>These are only activated if you opt in:</p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          <strong>Google AdSense</strong> — may serve ads and set cookies for ad
          personalization. The AdSense script is not loaded unless you consent
          to advertising cookies.
        </li>
      </ul>

      <h3 className="text-xl font-semibold text-cyan-200 mt-6 mb-3">
        3.4 Third-Party Embeds (No Cookies From Us)
      </h3>
      <p>
        These services are used on the site but are not gated by our consent
        banner because they do not set tracking cookies through our site:
      </p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          <strong>Giscus</strong> — powers the comment system on blog posts.
          Loads a script from <code>giscus.app</code> which connects to GitHub
          Discussions. If you interact with comments, Giscus and GitHub may set
          their own cookies. We do not control those.
        </li>
        <li>
          <strong>Pexels</strong> — some images on the site are served from
          Pexels. Loading these images means your browser makes requests to
          Pexels servers, which may log your IP. Pexels has its own privacy
          policy.
        </li>
      </ul>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        4. Cookie Reference Table
      </h2>
      <div className="overflow-x-auto my-4">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-gray-600">
              <th className="px-3 py-2">Cookie / Storage</th>
              <th className="px-3 py-2">Provider</th>
              <th className="px-3 py-2">Purpose</th>
              <th className="px-3 py-2">Duration</th>
              <th className="px-3 py-2">Requires Consent</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-gray-700">
              <td className="px-3 py-2">
                <code>cookieConsent</code>
              </td>
              <td className="px-3 py-2">{SITE_NAME} (localStorage)</td>
              <td className="px-3 py-2">Stores your consent preferences</td>
              <td className="px-3 py-2">Persistent</td>
              <td className="px-3 py-2">No (essential)</td>
            </tr>
            <tr className="border-b border-gray-700">
              <td className="px-3 py-2">
                <code>cookieConsentDate</code>
              </td>
              <td className="px-3 py-2">{SITE_NAME} (localStorage)</td>
              <td className="px-3 py-2">Records when consent was given</td>
              <td className="px-3 py-2">Persistent</td>
              <td className="px-3 py-2">No (essential)</td>
            </tr>
            <tr className="border-b border-gray-700">
              <td className="px-3 py-2">
                <code>_ga</code>
              </td>
              <td className="px-3 py-2">Google Analytics 4</td>
              <td className="px-3 py-2">Unique user identifier</td>
              <td className="px-3 py-2">2 years</td>
              <td className="px-3 py-2">Yes (analytics)</td>
            </tr>
            <tr className="border-b border-gray-700">
              <td className="px-3 py-2">
                <code>_ga_*</code>
              </td>
              <td className="px-3 py-2">Google Analytics 4</td>
              <td className="px-3 py-2">Groups events by measurement ID</td>
              <td className="px-3 py-2">2 years</td>
              <td className="px-3 py-2">Yes (analytics)</td>
            </tr>
            <tr>
              <td className="px-3 py-2">AdSense IDs</td>
              <td className="px-3 py-2">Google AdSense</td>
              <td className="px-3 py-2">Ad personalization and serving</td>
              <td className="px-3 py-2">Varies</td>
              <td className="px-3 py-2">Yes (advertising)</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        5. What We Do Not Do
      </h2>
      <p>To be clear:</p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          We do not load Google Analytics or AdSense scripts unless you
          explicitly consent. If you reject all, those scripts never touch your
          browser.
        </li>
        <li>We do not sell your personal data.</li>
        <li>We do not use third-party ad networks without your consent.</li>
        <li>
          We do not use fingerprinting or any other tracking that bypasses your
          cookie preferences.
        </li>
      </ul>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        6. Managing Your Preferences
      </h2>
      <p>You can change your mind at any time:</p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          Clear your browser&apos;s localStorage for this site — the consent
          banner will reappear on your next visit.
        </li>
        <li>
          Use your browser&apos;s built-in cookie controls to delete any cookies
          set by third parties.
        </li>
        <li>
          Contact us at{' '}
          <a
            href={`mailto:${PRIVACY_EMAIL}`}
            className="text-cyan-400 hover:text-cyan-300"
          >
            {PRIVACY_EMAIL}
          </a>{' '}
          if you need help.
        </li>
      </ul>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        7. Your Rights
      </h2>
      <p>
        <strong>If you are in the EU (GDPR):</strong> You have the right to
        access, rectify, erase, restrict, port, and object to processing of your
        personal data. You can withdraw consent at any time without affecting
        the lawfulness of prior processing. You also have the right to lodge a
        complaint with your local Data Protection Authority.
      </p>
      <p className="mt-4">
        <strong>If you are in California (CCPA):</strong> You have the right to
        know what personal information is collected, request its deletion, and
        opt out of its sale. We do not sell personal information.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        8. Data Processors
      </h2>
      <p>
        When you consent to analytics or advertising, your data may be processed
        by:
      </p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          <strong>Google LLC</strong> — for GA4 and/or AdSense. Data may be
          processed in the United States under Standard Contractual Clauses.
        </li>
        <li>
          <strong>Vercel, Inc.</strong> — hosts the site and may process IP
          addresses for infrastructure and security.
        </li>
        <li>
          <strong>GitHub / Giscus</strong> — if you use the comment system on
          blog posts.
        </li>
        <li>
          <strong>Lemon Squeezy LLC</strong> — processes payments as our merchant
          of record. May set cookies for fraud prevention and checkout session
          management when you make a purchase.
        </li>
      </ul>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        9. Updates to This Policy
      </h2>
      <p>
        If we change this policy, we will update the date at the top. We are not
        going to email you about cookie policy changes — just check back here if
        you are curious.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        10. Contact
      </h2>
      <p>Questions? Reach out:</p>
      <div className="bg-gray-800 p-4 rounded-lg mt-4 mb-4">
        <p>
          <strong>{COMPANY_NAME}</strong>
          <br />
          Email:{' '}
          <a
            href={`mailto:${OWNER_EMAIL}`}
            className="text-cyan-400 hover:text-cyan-300"
          >
            {OWNER_EMAIL}
          </a>{' '}
          or{' '}
          <a
            href={`mailto:${PRIVACY_EMAIL}`}
            className="text-cyan-400 hover:text-cyan-300"
          >
            {PRIVACY_EMAIL}
          </a>
          <br />
          Website:{' '}
          <a
            href={SITE_URL}
            className="text-cyan-400 hover:text-cyan-300"
          >
            {SITE_URL}
          </a>
        </p>
      </div>
    </div>
  );
}
