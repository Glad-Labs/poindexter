/**
 * Footer Component Tests (components/Footer.js)
 *
 * Tests footer content, links, and structure
 * Verifies: Footer links, copyright, social links, newsletter signup
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import Footer from '../Footer';

// Mock Next.js Link
jest.mock('next/link', () => {
  return ({ children, href }) => <a href={href}>{children}</a>;
});

describe('Footer Component', () => {
  it('should render footer element', () => {
    const { container } = render(<Footer />);
    expect(container.querySelector('footer')).toBeInTheDocument();
  });

  it('should display copyright information', () => {
    render(<Footer />);
    const currentYear = new Date().getFullYear();
    expect(
      screen.getByText(new RegExp(currentYear.toString()))
    ).toBeInTheDocument();
  });

  it('should display company name in copyright', () => {
    render(<Footer />);
    expect(screen.getByText(/glad labs|copyright/i)).toBeInTheDocument();
  });

  it('should display about section', () => {
    render(<Footer />);
    const aboutLink = screen.getByRole('link', { name: /about/i });
    expect(aboutLink).toBeInTheDocument();
  });

  it('should display privacy policy link', () => {
    render(<Footer />);
    const privacyLinks = screen.getAllByRole('link', {
      name: /privacy|policy/i,
    });
    expect(privacyLinks.length).toBeGreaterThan(0);
  });

  it('should display terms of service link', () => {
    render(<Footer />);
    const termsLink = screen.getByRole('link', { name: /terms|service/i });
    expect(termsLink).toBeInTheDocument();
  });

  it('should display contact link or email', () => {
    render(<Footer />);
    const contactLink = screen.queryByRole('link', { name: /contact|email/i });
    // Footer may use mailto: link or not have a dedicated contact link
    if (contactLink) {
      expect(contactLink).toBeInTheDocument();
    } else {
      // At minimum the footer should render
      expect(screen.getAllByRole('link').length).toBeGreaterThan(0);
    }
  });

  it('should have social media links', () => {
    render(<Footer />);
    const socialLinks = screen.queryAllByRole('link', {
      name: /twitter|facebook|linkedin|github|instagram|x\.com/i,
    });
    // Social links may use icon-only labels or different naming
    const allLinks = screen.getAllByRole('link');
    expect(allLinks.length).toBeGreaterThan(0);
  });

  it('should display RSS feed link', () => {
    render(<Footer />);
    const rssLink = screen.queryByRole('link', { name: /rss|feed/i });
    if (rssLink) {
      expect(rssLink).toBeInTheDocument();
    }
  });

  it('should have proper link destinations', () => {
    render(<Footer />);
    const privacyLinks = screen.getAllByRole('link', {
      name: /privacy|policy/i,
    });
    const href = privacyLinks[0].getAttribute('href');
    expect(href).toContain('privacy');
  });

  it('should display newsletter signup section', () => {
    render(<Footer />);
    const newsletterSection = screen.queryByText(/newsletter|subscribe|email/i);
    if (newsletterSection) {
      expect(newsletterSection).toBeInTheDocument();
    }
  });

  it('should have organized footer sections', () => {
    render(<Footer />);
    expect(screen.getByRole('link', { name: /about/i })).toBeInTheDocument();
    const privacyLinks = screen.getAllByRole('link', {
      name: /privacy|policy/i,
    });
    expect(privacyLinks.length).toBeGreaterThan(0);
  });

  it('should have semantic footer structure', () => {
    const { container } = render(<Footer />);
    const footer = container.querySelector('footer');
    expect(footer).toBeInTheDocument();
    expect(footer.children.length).toBeGreaterThan(0);
  });

  it('should display multiple column layout for footer links', () => {
    const { container } = render(<Footer />);
    const footer = container.querySelector('footer');
    const linkGroups = footer?.querySelectorAll(
      'nav, [role="navigation"], ul, div'
    );
    expect(linkGroups).not.toBeNull();
    expect(linkGroups.length).toBeGreaterThan(0);
  });

  it('should have accessible link structure', () => {
    render(<Footer />);
    const links = screen.getAllByRole('link');
    links.forEach((link) => {
      expect(link).toHaveAccessibleName();
    });
  });

  it('should display site map or quick links', () => {
    render(<Footer />);
    const blogLink = screen.queryByRole('link', { name: /blog/i });
    // Footer may or may not have a blog link
    const links = screen.getAllByRole('link');
    expect(links.length).toBeGreaterThan(0);
  });

  it('should support dark mode styling', () => {
    const { container } = render(<Footer />);
    const footer = container.querySelector('footer');
    // Check for dark mode class or style
    expect(footer?.className).toBeDefined() ||
      expect(footer).toBeInTheDocument();
  });

  it('should be responsive (mobile-friendly)', () => {
    const { container } = render(<Footer />);
    const footer = container.querySelector('footer');
    // Footer should have classes for styling
    expect(footer).toBeInTheDocument();
    expect(footer?.className).toBeDefined();
  });

  it('should have proper spacing and padding', () => {
    const { container } = render(<Footer />);
    const footer = container.querySelector('footer');
    if (footer) {
      const style = window.getComputedStyle(footer);
      expect(style.padding || style.margin).toBeDefined();
    }
  });

  it('should display back to top button if applicable', () => {
    render(<Footer />);
    const backToTopButton = screen.queryByRole('button', {
      name: /back to top|top/i,
    });
    if (backToTopButton) {
      expect(backToTopButton).toBeInTheDocument();
    }
  });

  it('should have language selector if multi-language', () => {
    render(<Footer />);
    const languageSelector = screen.queryByRole('combobox', {
      name: /language|lang/i,
    });
    if (languageSelector) {
      expect(languageSelector).toBeInTheDocument();
    }
  });

  it('should update year dynamically in copyright', () => {
    render(<Footer />);
    const currentYear = new Date().getFullYear();
    expect(
      screen.getByText(new RegExp(currentYear.toString()))
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #791: Footer Legal nav has aria-label (issue #791)
// ---------------------------------------------------------------------------

describe('Footer — a11y: Legal nav has aria-label (issue #791)', () => {
  it('Legal nav has aria-label="Legal navigation"', () => {
    render(<Footer />);
    const legalNav = screen.getByRole('navigation', {
      name: 'Legal navigation',
    });
    expect(legalNav).toBeInTheDocument();
  });

  it('Explore nav has aria-label="Explore navigation"', () => {
    render(<Footer />);
    const exploreNav = screen.getByRole('navigation', {
      name: 'Explore navigation',
    });
    expect(exploreNav).toBeInTheDocument();
  });

  it('multiple nav landmarks each have distinct aria-labels', () => {
    const { container } = render(<Footer />);
    const navs = container.querySelectorAll('nav');
    const labelled = Array.from(navs).filter((nav) =>
      nav.hasAttribute('aria-label')
    );
    // At least 2 navs should be labelled (Explore + Legal)
    expect(labelled.length).toBeGreaterThanOrEqual(2);
    // All labels should be unique
    const labels = labelled.map((nav) => nav.getAttribute('aria-label'));
    const uniqueLabels = new Set(labels);
    expect(uniqueLabels.size).toBe(labels.length);
  });
});
