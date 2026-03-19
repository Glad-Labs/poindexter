import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Header from '../TopNav';

// Mock next/link
jest.mock('next/link', () => {
  return ({ children, href, ...props }) => (
    <a href={href} {...props}>
      {children}
    </a>
  );
});

describe('Header Component', () => {
  beforeEach(() => {
    // Reset scroll position before each test
    window.scrollY = 0;
  });

  test('renders navigation links', () => {
    render(<Header />);

    expect(screen.getByText('Articles')).toBeInTheDocument();
    expect(screen.getByText('About')).toBeInTheDocument();
    expect(screen.getByText(/Explore|Read/)).toBeInTheDocument();
  });

  test('renders logo', () => {
    render(<Header />);

    const logo = screen.getByText('GL');
    expect(logo).toBeInTheDocument();
  });

  test('renders brand logo with accessible name', () => {
    render(<Header />);

    const brandLink = screen.getByRole('link', { name: /glad labs/i });
    expect(brandLink).toBeInTheDocument();
  });

  test('has correct link href attributes', () => {
    render(<Header />);

    const archiveLink = screen.getByRole('link', { name: /Articles/ });
    expect(archiveLink).toHaveAttribute('href', '/archive/1');
  });

  test('header has correct aria role', () => {
    const { container } = render(<Header />);
    const header = container.querySelector('header');

    expect(header).toBeInTheDocument();
  });

  test('applies scroll styles when scrolled', async () => {
    const { container } = render(<Header />);
    const header = container.querySelector('header');

    // Initial state - not scrolled
    let className = header.getAttribute('class');
    expect(className).not.toContain('shadow-lg');

    // Simulate scroll
    window.scrollY = 50;
    fireEvent.scroll(window, { target: { scrollY: 50 } });

    await waitFor(
      () => {
        className = header.getAttribute('class');
        expect(className).toContain('backdrop-blur-xl');
      },
      { timeout: 300 }
    );
  });

  test('button is accessible with keyboard', () => {
    render(<Header />);

    const button = screen.getByRole('link', { name: /Explore|Read/ });
    expect(button).toHaveAttribute('href', '/archive/1');
  });
});
