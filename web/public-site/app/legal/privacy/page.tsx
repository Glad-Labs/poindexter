import type { Metadata } from 'next';
import { Eyebrow } from '@glad-labs/brand';
import { FAQSchema } from '../../../components/StructuredData';
import LegalProse from '../_components/LegalProse';
import {
  SITE_NAME,
  SITE_URL,
  COMPANY_NAME,
  SUPPORT_EMAIL,
  PRIVACY_EMAIL,
} from '@/lib/site.config';

export const metadata: Metadata = {
  title: `Privacy Policy - ${SITE_NAME}`,
  description: `Privacy Policy for ${SITE_NAME}`,
  alternates: { canonical: `${SITE_URL}/legal/privacy` },
};

export default function PrivacyPolicy() {
  const lastUpdated = new Date('2026-09-28').toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  const faqs = [
    {
      question: 'How long do you keep my data?',
      answer:
        'Error reports: 90 days. Server logs: 90 days. If you consent to Google Analytics: 14 months. If you consent to AdSense: up to 30 months. Newsletter: your email address and name are kept while you are subscribed, and after you unsubscribe we keep the address, marked as unsubscribed, so we do not email you again. We do not currently delete newsletter records on a schedule; you can ask us to delete them at any time.',
    },
    {
      question: 'What third parties have access to my data?',
      answer:
        'Always active: Vercel (Hosting), Cloudflare (Error Report Relay), GitHub (Giscus Comments). Error reports themselves go to a tracker we host ourselves. Consent-gated: Google (Analytics & AdSense, only if you opt in). Only if you subscribe to the newsletter: Resend (email delivery), and Cloudflare also runs our unsubscribe page. Each has their own privacy policies.',
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
      answer: `Email us at ${PRIVACY_EMAIL} with any privacy questions. We aim to respond within 30 days per GDPR requirements.`,
    },
  ];

  return (
    <>
      <FAQSchema faqs={faqs} />
      <LegalProse>
        <Eyebrow>GLAD LABS · LEGAL</Eyebrow>
        <h1>Privacy Policy</h1>

        <p className="gl-mono gl-mono--upper text-xs text-[color:var(--gl-text-muted)]">
          Last Updated · {lastUpdated}
        </p>

        <h2>1. Introduction</h2>
        <p>
          {COMPANY_NAME} (&quot;we,&quot; &quot;us,&quot; &quot;our&quot;)
          respects your privacy. This policy explains what data we collect when
          you visit gladlabs.io, how we use it, and your rights regarding that
          data. We keep it straightforward because privacy policies
          shouldn&apos;t require a law degree to understand.
        </p>

        <h2>2. Legal Basis for Processing (GDPR)</h2>
        <p>
          Under the GDPR, we process your personal data based on the following
          legal bases:
        </p>
        <ul>
          <li>
            <strong>Consent (Article 6(1)(a)):</strong> Google Analytics and
            AdSense are only loaded after you explicitly opt in via our cookie
            banner. You consent to our newsletter by submitting the signup form
            (see <a href="#newsletter">3.4</a>)
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
            <strong>Legitimate Interest (Article 6(1)(f)):</strong> Error
            monitoring and site optimization
          </li>
        </ul>

        <h2>3. Information We Collect</h2>
        <p>We collect minimal data. Here&apos;s exactly what and why:</p>

        <h3>3.1 Always-Active Data Collection</h3>
        <ul>
          <li>
            <strong>Error Monitoring:</strong> When something breaks, the site
            sends an error report (the error message and stack trace, the page
            address, and your browser and operating system) through a Cloudflare
            relay to an error tracker we host ourselves. Reports do not include
            your IP address: the relay uses it only to rate-limit requests and
            does not store it. This helps us fix bugs fast. Legal basis:
            Legitimate Interest (Article 6(1)(f)).
          </li>
          <li>
            <strong>Server Logs:</strong> Our hosting provider (Vercel)
            automatically logs IP addresses, browser type, and pages visited for
            security and operational purposes.
          </li>
        </ul>

        <h3>3.2 Consent-Gated Data Collection</h3>
        <p>
          The following services are only activated if you explicitly consent
          via our cookie banner:
        </p>
        <ul>
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

        <h3>3.3 Third-Party Services</h3>
        <ul>
          <li>
            <strong>Giscus (Comments):</strong> Our blog uses Giscus, a
            commenting system powered by GitHub Discussions. When you comment,
            you authenticate via GitHub. Your GitHub username, avatar, and
            comment content are stored on GitHub&apos;s servers. Giscus does not
            use cookies or track you beyond the comment interaction.
          </li>
        </ul>

        <h3 id="newsletter" className="scroll-mt-24">
          3.4 Newsletter (Only If You Subscribe)
        </h3>
        <p>
          If you sign up for updates, the form asks for your email address and,
          if you choose, your first and last name. That is all it asks for, and
          we use it only to send you the newsletter. The form does not store
          your IP address or browser details with your subscription (the server
          logs described in 3.1 still record the request itself). Legal basis:
          Consent (Article 6(1)(a)), which you give by submitting the form and
          can withdraw at any time by unsubscribing.
        </p>
        <ul>
          <li>
            <strong>Where it goes:</strong> Our website (hosted on Vercel) sends
            your details to Resend, the email service we use, which stores them
            as a contact. Our own system then copies each new contact into our
            subscriber list, which is what we send the newsletter from.
          </li>
          <li>
            <strong>What we store:</strong> Your email address; your first and
            last name, if you gave them; when you signed up; a private random
            code used only for your unsubscribe link; and, if you unsubscribe,
            when you did.
          </li>
          <li>
            <strong>Welcome email:</strong> When you sign up, we also send you a
            welcome email through Resend.
          </li>
          <li>
            <strong>Newsletter emails:</strong> Each time we publish a new post,
            we email a link to it, through Resend, to everyone who is
            subscribed. If you gave a first name, we use it to greet you. Every
            newsletter email has an unsubscribe link at the bottom.
          </li>
          <li>
            <strong>Send and delivery records:</strong> We keep a log of which
            emails we sent you and whether Resend accepted them. Resend also
            reports whether each email was delivered, delayed, bounced, failed,
            or reported as spam, and we record that against your address.
          </li>
          <li>
            <strong>Unsubscribing:</strong> The unsubscribe link opens a page
            where you confirm; nothing changes until you do. That page runs on
            Cloudflare, which keeps a note of your request (your private
            unsubscribe code and the time, not your email address) until our
            system applies it, normally within minutes; it uses your IP address
            only to rate-limit requests. An email already being sent when you
            unsubscribe may still reach you.
          </li>
          <li>
            <strong>After you unsubscribe:</strong> Unsubscribing does not
            delete anything. We keep your address on our list, marked as
            unsubscribed, so we do not email you again and the address is not
            added back automatically. The contact in Resend is not removed
            either. To have your newsletter data deleted, use our{' '}
            <a href="/legal/data-requests">Data Request page</a>. Section 9
            covers how long we keep it. Legal basis for keeping this record:
            Legitimate Interest (Article 6(1)(f)), in making sure we honor your
            request not to be emailed.
          </li>
        </ul>

        <h2>4. How We Use Your Information</h2>
        <p>We use collected data to:</p>
        <ul>
          <li>
            Understand which content performs well (first-party analytics)
          </li>
          <li>Fix errors and improve site reliability (error monitoring)</li>
          <li>Ensure security and prevent abuse (server logs)</li>
          <li>Send you our welcome email and newsletter, if you subscribe</li>
          <li>Comply with legal obligations</li>
        </ul>
        <p>
          Google Analytics and AdSense are only active if you consent. If you
          reject those categories, we collect zero third-party tracking data.
        </p>

        <h2>5. Information Sharing &amp; Disclosure</h2>
        <p>
          We do <strong>NOT</strong> sell, trade, or rent your personal
          information. Period. We share data only with:
        </p>
        <ul>
          <li>
            <strong>Service Providers:</strong> Vercel (hosting), Cloudflare
            (error report relay, and the newsletter unsubscribe page), GitHub
            (comments), Resend (newsletter email) — only the data necessary for
            them to provide their services.
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

        <h2>6. Cookies</h2>
        <p>
          We use minimal cookies. Essential cookies are required for the site to
          function. We do not use third-party advertising or tracking cookies.
          See our <a href="/legal/cookie-policy">Cookie Policy</a> for full
          details.
        </p>

        <h2>7. Your Privacy Rights</h2>
        <p>You have the right to:</p>
        <ul>
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
          <a href="/legal/data-requests">Data Request page</a> or by emailing{' '}
          {PRIVACY_EMAIL}.
        </p>

        <h2>8. Data Security</h2>
        <p>
          We use appropriate technical and organizational measures to protect
          your data. Our infrastructure runs on Vercel and Cloudflare with HTTPS
          everywhere. That said, no system is 100% secure — we&apos;re honest
          about that.
        </p>

        <h2>9. Data Retention</h2>
        <p>We keep data only as long as necessary:</p>
        <ul>
          <li>
            <strong>Google Analytics Data:</strong> If consented, retained for
            up to 14 months by Google.
          </li>
          <li>
            <strong>Google AdSense Data:</strong> If consented, advertising
            cookies retained for up to 30 months.
          </li>
          <li>
            <strong>Error Reports:</strong> Retained for 90 days in our
            self-hosted error tracker. A report waits at most 7 days in the
            Cloudflare relay before it is delivered or deleted.
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
            <strong>Newsletter Subscription:</strong> Kept for as long as you
            stay subscribed. After you unsubscribe we keep your address, marked
            as unsubscribed, so we do not email you again. We do not currently
            delete these records on a schedule, so they stay until we delete
            them, for example when you ask us to. The matching contact in Resend
            stays until we delete it too.
          </li>
          <li>
            <strong>Newsletter Send &amp; Delivery Records:</strong> The log of
            which emails we sent you, and the delivery results Resend reports
            back, are not deleted on a schedule today either. Resend keeps its
            own logs of the emails it sends for us, under its own retention
            rules.
          </li>
          <li>
            <strong>Purchase Data:</strong> Transaction records, billing
            details, and purchase history are retained by Lemon Squeezy as
            merchant of record in accordance with their retention policy and
            applicable tax and accounting requirements.
          </li>
        </ul>

        <h2>10. Data Processors &amp; Third Parties</h2>
        <p>The following third parties may process your data:</p>
        <table>
          <thead>
            <tr>
              <th>Company</th>
              <th>Service</th>
              <th>Data Processed</th>
              <th>Privacy Policy</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Google LLC</td>
              <td>Analytics &amp; AdSense (consent-gated)</td>
              <td>Pages visited, interactions, ad personalization</td>
              <td>
                <a href="https://policies.google.com/privacy">View Policy</a>
              </td>
            </tr>
            <tr>
              <td>Vercel Inc</td>
              <td>Hosting</td>
              <td>Server logs, IP addresses</td>
              <td>
                <a href="https://vercel.com/legal/privacy">View Policy</a>
              </td>
            </tr>
            <tr>
              <td>Cloudflare Inc</td>
              <td>
                Error report relay; newsletter unsubscribe page (if you
                subscribe)
              </td>
              <td>
                Error reports: browser info, page address, error data.
                Unsubscribe page: unsubscribe code, time of request. The IP
                address is used only to rate-limit requests, not stored
              </td>
              <td>
                <a href="https://www.cloudflare.com/privacypolicy/">
                  View Policy
                </a>
              </td>
            </tr>
            <tr>
              <td>GitHub Inc</td>
              <td>Comments (Giscus)</td>
              <td>GitHub username, avatar, comments</td>
              <td>
                <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">
                  View Policy
                </a>
              </td>
            </tr>
            <tr>
              <td>Lemon Squeezy LLC</td>
              <td>Payment Processing (Merchant of Record)</td>
              <td>
                Name, email, billing address, payment method, purchase history,
                IP address
              </td>
              <td>
                <a href="https://www.lemonsqueezy.com/privacy">View Policy</a>
              </td>
            </tr>
            <tr>
              <td>Resend (Plus Five Five, Inc.)</td>
              <td>Email delivery (newsletter, if you subscribe)</td>
              <td>
                Email address, name, the emails we send you, delivery results
              </td>
              <td>
                <a href="https://resend.com/legal/privacy-policy">
                  View Policy
                </a>
              </td>
            </tr>
          </tbody>
        </table>

        <h2>11. International Data Transfers</h2>
        <p>
          Your data may be processed in the United States by our service
          providers (Vercel, Cloudflare, Lemon Squeezy, Resend, and Google if
          you consent). These transfers are protected by Standard Contractual
          Clauses (SCCs) where applicable.
        </p>

        <h2>12. Automated Decision Making</h2>
        <p>
          We do not use automated decision-making or profiling that produces
          legal or similarly significant effects about you.
        </p>

        <h2>13. Children&apos;s Privacy</h2>
        <p>
          We do not knowingly collect personal information from children under
          13 (or 16 in the EU). If we learn we&apos;ve collected data from a
          child under these ages, we&apos;ll delete it promptly. Parents: if you
          believe your child&apos;s information was collected, contact us at{' '}
          {PRIVACY_EMAIL}.
        </p>

        <h2>14. Third-Party Links</h2>
        <p>
          Our site links to external websites. We&apos;re not responsible for
          their privacy practices. Check their policies before sharing personal
          information.
        </p>

        <h2>15. Contact Us</h2>
        <p>Questions about privacy? Get in touch:</p>
        <blockquote>
          <p>
            <strong>{COMPANY_NAME}</strong>
            <br />
            Privacy Email:{' '}
            <a href={`mailto:${PRIVACY_EMAIL}`}>{PRIVACY_EMAIL}</a>
            <br />
            General Email: {SUPPORT_EMAIL}
            <br />
            Data Requests: <a href="/legal/data-requests">Submit a request</a>
          </p>
        </blockquote>
        <p className="gl-mono gl-mono--upper text-xs text-[color:var(--gl-text-muted)]">
          Response Time · 30 days per GDPR. If you&apos;re not satisfied, you
          may lodge a complaint with your local data protection authority.
        </p>

        <h2>16. Policy Updates</h2>
        <p>
          We may update this policy. Changes get posted here with an updated
          date. Continued use of the site means you accept the current version.
        </p>
      </LegalProse>
    </>
  );
}
