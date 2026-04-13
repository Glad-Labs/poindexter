import type { Metadata } from 'next';
import { SITE_NAME, SITE_URL, COMPANY_NAME, SUPPORT_EMAIL } from '@/lib/site.config';

export const metadata: Metadata = {
  title: `Terms of Service - ${SITE_NAME}`,
  description: `Terms of Service for ${SITE_NAME}`,
};

export default function TermsOfService() {
  const lastUpdated = new Date('2026-04-13').toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  return (
    <div className="prose prose-invert max-w-none">
      <h1 className="text-4xl font-bold text-cyan-400 mb-4">
        Terms of Service
      </h1>

      <p className="text-gray-400 mb-8">
        <strong>Last Updated:</strong> {lastUpdated}
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        1. Agreement to Terms
      </h2>
      <p>
        By accessing gladlabs.io ("the Site"), you agree to these terms. If you
        don&apos;t agree, don&apos;t use the site. Simple as that.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        2. What You Can Do
      </h2>
      <p>
        You&apos;re welcome to read, share, and learn from our content. You may
        not:
      </p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>Copy or republish our content without attribution</li>
        <li>Use our content for commercial purposes without permission</li>
        <li>
          Attempt to reverse engineer, scrape, or exploit any software on the
          site
        </li>
        <li>Remove any copyright or proprietary notices from our materials</li>
        <li>Mirror the site or redistribute materials on another server</li>
      </ul>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        3. AI-Generated Content
      </h2>
      <p>
        Some content on this site is produced or assisted by AI systems. All
        published content is reviewed and approved by {SITE_NAME} before
        publication. We stand behind what we publish, regardless of how it was
        produced. That said, AI-generated content may contain errors — use your
        judgment.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        4. Disclaimer
      </h2>
      <p>
        Everything on this site is provided &quot;as is.&quot; We share what we
        know and what we&apos;re building, but we make no warranties — expressed
        or implied — about accuracy, completeness, or fitness for any particular
        purpose. Use the information at your own discretion.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        5. Limitations of Liability
      </h2>
      <p>
        {SITE_NAME} is not liable for any damages arising from your use of (or
        inability to use) this site or its content. This includes loss of data,
        profit, or business interruption — even if we&apos;ve been told it might
        happen.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        6. Accuracy of Materials
      </h2>
      <p>
        We do our best to keep things accurate, but technical content moves
        fast. Articles may reference tools, APIs, or configurations that have
        changed since publication. We may update content at any time without
        notice.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        7. External Links
      </h2>
      <p>
        We link to third-party sites for reference. We don&apos;t control those
        sites and aren&apos;t responsible for their content. A link isn&apos;t
        an endorsement.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        8. Purchases & Payments
      </h2>
      <p>
        Some products and subscriptions are available for purchase through {SITE_NAME},
        including digital guides and premium prompt packs. All payments are processed by
        Lemon Squeezy (Lemon Squeezy LLC), our merchant of record. When you make a purchase:
      </p>
      <ul className="list-disc list-inside space-y-2 mb-4">
        <li>
          Lemon Squeezy handles all payment processing, billing, and tax
          collection as the merchant of record
        </li>
        <li>
          Your payment information is collected and stored by Lemon Squeezy, not
          by {SITE_NAME} — we never see your full card details
        </li>
        <li>
          Digital products are delivered immediately after purchase and are
          non-refundable unless the product is materially defective
        </li>
        <li>
          Subscriptions may be cancelled at any time through your Lemon Squeezy
          customer portal — cancellation takes effect at the end of the current
          billing period
        </li>
        <li>
          Prices are listed in USD and may be subject to applicable sales tax or
          VAT, collected by Lemon Squeezy based on your location
        </li>
      </ul>
      <p>
        By making a purchase, you also agree to Lemon Squeezy&apos;s{' '}
        <a
          href="https://www.lemonsqueezy.com/terms"
          className="text-cyan-400 hover:text-cyan-300"
          target="_blank"
          rel="noopener noreferrer"
        >
          Terms of Service
        </a>.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        9. User Content
      </h2>
      <p>
        If you submit content to {SITE_NAME} (e.g., comments via Giscus), you
        represent that it&apos;s your original work, doesn&apos;t violate
        anyone&apos;s rights, and doesn&apos;t contain anything defamatory,
        obscene, or unlawful.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        10. Intellectual Property
      </h2>
      <p>
        All content on gladlabs.io — text, graphics, logos, images, and software
        — is the property of {SITE_NAME} or its content suppliers and is protected
        by copyright law. You&apos;re not granted any rights beyond normal
        viewing and personal use.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        11. Modifications
      </h2>
      <p>
        We may update these terms at any time. Continued use of the site means
        you accept the current version. We&apos;ll update the date at the top
        when changes are made.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        12. Governing Law
      </h2>
      <p>
        These terms are governed by the laws of the United States. Any disputes
        will be resolved in the courts of the applicable jurisdiction.
      </p>

      <h2 className="text-2xl font-bold text-cyan-300 mt-8 mb-4">
        13. Contact
      </h2>
      <p>Questions about these terms? Reach out:</p>
      <div className="bg-gray-800 p-4 rounded-lg mt-4 mb-4">
        <p>
          <strong>{COMPANY_NAME}</strong>
          <br />
          Email: {SUPPORT_EMAIL}
          <br />
          Website: {SITE_URL}
        </p>
      </div>
    </div>
  );
}
