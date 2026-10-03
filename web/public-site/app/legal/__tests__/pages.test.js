/**
 * Legal Pages Tests
 *
 * Covers:
 * - Terms of Service: renders with heading and content
 * - Privacy Policy: renders with heading and content, and describes the
 *   newsletter (the section the signup form links to, the legal basis, every
 *   provider the signup route sends data to, retention, transfers, FAQ)
 * - Cookie Policy: renders with heading and content
 * - Data Requests: renders with heading and content
 */

import React from 'react';
import fs from 'fs';
import path from 'path';
import { render, screen, within } from '@testing-library/react';
import NewsletterModal from '../../../components/NewsletterModal';

// Mock StructuredData components used by privacy page. FAQSchema only emits
// JSON-LD, so it renders nothing; it keeps the FAQs it was handed so the
// answers can be checked.
const mockFaqs = { current: [] };
jest.mock('@/components/StructuredData', () => ({
  FAQSchema: ({ faqs }) => {
    mockFaqs.current = faqs;
    return null;
  },
  BlogPostingSchema: () => null,
  BreadcrumbSchema: () => null,
}));

// The elements between the <h2> whose text matches `headingRe` and the next
// <h2>: one numbered section of a legal page.
function sectionNodes(container, headingRe) {
  const start = Array.from(container.querySelectorAll('h2')).find((h) =>
    headingRe.test(h.textContent)
  );
  if (!start) throw new Error(`no <h2> matches ${headingRe}`);
  const nodes = [];
  for (
    let el = start.nextElementSibling;
    el && el.tagName !== 'H2';
    el = el.nextElementSibling
  ) {
    nodes.push(el);
  }
  return nodes;
}

const sectionText = (container, headingRe) =>
  sectionNodes(container, headingRe)
    .map((el) => el.textContent)
    .join(' ');

describe('Legal Pages', () => {
  describe('Terms of Service', () => {
    let TermsOfService;

    beforeAll(async () => {
      const mod = await import('../terms/page');
      TermsOfService = mod.default;
    });

    it('renders without crashing', () => {
      const { container } = render(<TermsOfService />);
      expect(container.firstChild).toBeTruthy();
    });

    it('renders the Terms of Service heading', () => {
      const { container } = render(<TermsOfService />);
      // Use querySelector to avoid multiple-match errors (navigation also contains the text)
      expect(container.querySelector('h1')).toHaveTextContent(
        /Terms of Service/i
      );
    });

    it('has a proper heading hierarchy', () => {
      const { container } = render(<TermsOfService />);
      expect(container.querySelector('h1')).toBeInTheDocument();
    });

    it('displays a last updated date', () => {
      render(<TermsOfService />);
      expect(screen.getByText(/last updated/i)).toBeInTheDocument();
    });
  });

  describe('Privacy Policy', () => {
    let PrivacyPolicy;

    beforeAll(async () => {
      const mod = await import('../privacy/page');
      PrivacyPolicy = mod.default;
    });

    it('renders without crashing', () => {
      const { container } = render(<PrivacyPolicy />);
      expect(container.firstChild).toBeTruthy();
    });

    it('renders the Privacy Policy heading', () => {
      const { container } = render(<PrivacyPolicy />);
      expect(container.querySelector('h1')).toHaveTextContent(
        /Privacy Policy/i
      );
    });

    it('has a proper heading hierarchy', () => {
      const { container } = render(<PrivacyPolicy />);
      expect(container.querySelector('h1')).toBeInTheDocument();
    });

    // These pin the facts the newsletter section has to keep stating, not the
    // wording around them. The policy once said nothing about the newsletter
    // or the provider that sends it.
    describe('newsletter', () => {
      it('has a newsletter section', () => {
        const { container } = render(<PrivacyPolicy />);
        expect(container.querySelector('#newsletter')).toHaveTextContent(
          /newsletter/i
        );
      });

      it("is where the signup form's privacy policy link goes", () => {
        // Renders both, so renaming the id on either side fails here.
        const { container } = render(<PrivacyPolicy />);
        render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
        const link = within(screen.getByRole('dialog')).getByRole('link', {
          name: /privacy policy/i,
        });
        const url = new URL(link.getAttribute('href'), 'https://example.test');
        expect(url.pathname).toBe('/legal/privacy');
        expect(url.hash).toMatch(/^#.+/);
        expect(
          container.querySelector(`[id="${url.hash.slice(1)}"]`)
        ).not.toBeNull();
      });

      it('gives consent as the legal basis (section 2)', () => {
        const { container } = render(<PrivacyPolicy />);
        const consent = sectionNodes(container, /^2\./)
          .flatMap((el) => Array.from(el.querySelectorAll('li')))
          .find((li) => /Article 6\(1\)\(a\)/.test(li.textContent));
        expect(consent).toHaveTextContent(/newsletter/i);
      });

      it('names Resend where data is shared (section 5)', () => {
        const { container } = render(<PrivacyPolicy />);
        expect(sectionText(container, /^5\./)).toMatch(/resend/i);
      });

      it('says how long newsletter data is kept (section 9)', () => {
        const { container } = render(<PrivacyPolicy />);
        const retention = sectionText(container, /^9\./);
        expect(retention).toMatch(/newsletter/i);
        expect(retention).toMatch(/resend/i);
      });

      it('lists Resend in the processors table (section 10)', () => {
        const { container } = render(<PrivacyPolicy />);
        const row = within(container.querySelector('table'))
          .getAllByRole('row')
          .find((r) => /resend/i.test(r.textContent));
        expect(row).toBeDefined();
        expect(within(row).getByRole('link')).toHaveAttribute(
          'href',
          expect.stringMatching(/^https:\/\//)
        );
      });

      it('includes Resend in the international transfers (section 11)', () => {
        const { container } = render(<PrivacyPolicy />);
        expect(sectionText(container, /^11\./)).toMatch(/resend/i);
      });

      it('answers the retention and third-party FAQs for the newsletter', () => {
        render(<PrivacyPolicy />);
        const answer = (question) =>
          mockFaqs.current.find((f) => f.question === question)?.answer;
        expect(answer('How long do you keep my data?')).toMatch(/newsletter/i);
        expect(answer('What third parties have access to my data?')).toMatch(
          /resend/i
        );
      });

      it('names every provider the signup route sends data to', () => {
        // Derived from the route, not listed by hand: a provider added to or
        // swapped in the route has to reach the processors table too.
        const route = fs.readFileSync(
          path.join(__dirname, '../../api/newsletter/subscribe/route.ts'),
          'utf8'
        );
        const providers = [
          ...new Set(
            [...route.matchAll(/https:\/\/([a-z0-9-]+\.)+[a-z]{2,}/gi)].map(
              (m) => m[0].split('.').slice(-2)[0].toLowerCase()
            )
          ),
        ];
        // A scan that found nothing would pass vacuously.
        expect(providers).toContain('resend');
        const { container } = render(<PrivacyPolicy />);
        const table = container
          .querySelector('table')
          .textContent.toLowerCase();
        providers.forEach((provider) => expect(table).toContain(provider));
      });
    });
  });

  describe('Cookie Policy', () => {
    let CookiePolicy;

    beforeAll(async () => {
      const mod = await import('../cookie-policy/page');
      CookiePolicy = mod.default;
    });

    it('renders without crashing', () => {
      const { container } = render(<CookiePolicy />);
      expect(container.firstChild).toBeTruthy();
    });

    it('renders the Cookie Policy heading', () => {
      const { container } = render(<CookiePolicy />);
      expect(container.querySelector('h1')).toHaveTextContent(/Cookie Policy/i);
    });

    it('has a proper heading hierarchy', () => {
      const { container } = render(<CookiePolicy />);
      expect(container.querySelector('h1')).toBeInTheDocument();
    });
  });

  describe('Data Requests', () => {
    let DataRequests;

    beforeAll(async () => {
      const mod = await import('../data-requests/page');
      DataRequests = mod.default;
    });

    it('renders without crashing', () => {
      const { container } = render(<DataRequests />);
      expect(container.firstChild).toBeTruthy();
    });

    it('has a heading on the page', () => {
      const { container } = render(<DataRequests />);
      const headings = container.querySelectorAll('h1, h2');
      expect(headings.length).toBeGreaterThan(0);
    });

    it('lets a newsletter subscriber name their data', () => {
      // The privacy policy sends subscribers here to have it deleted.
      render(<DataRequests />);
      expect(
        screen.getByRole('checkbox', { name: /newsletter/i })
      ).toBeInTheDocument();
    });
  });
});
