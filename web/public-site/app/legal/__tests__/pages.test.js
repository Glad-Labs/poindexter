/**
 * Legal Pages Tests
 *
 * Covers:
 * - Terms of Service: renders with heading and content
 * - Privacy Policy: renders with heading and content
 * - Cookie Policy: renders with heading and content
 * - Data Requests: renders with heading and content
 */

import React from 'react';
import { render, screen } from '@testing-library/react';

// Mock StructuredData components used by privacy page
jest.mock('@/components/StructuredData', () => ({
  FAQSchema: () => null,
  BlogPostingSchema: () => null,
  BreadcrumbSchema: () => null,
}));

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
  });
});
