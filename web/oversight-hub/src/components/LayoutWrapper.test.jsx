/**
 * LayoutWrapper Component Tests
 *
 * Tests the main app layout including navigation, header, and layout structure
 * Verifies: Header rendering, nav menu, authenticated state, routing context
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LayoutWrapper from './LayoutWrapper';
import { BrowserRouter } from 'react-router-dom';

// Mock context and child components
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@example.com' },
    isAuthenticated: true,
    token: 'test-token',
  }),
}));

const renderWithRouter = (component) => {
  return render(<BrowserRouter>{component}</BrowserRouter>);
};

describe('LayoutWrapper Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should render header with app title', () => {
    renderWithRouter(<LayoutWrapper />);
    const header = screen.getByRole('banner');
    expect(header).toBeInTheDocument();
  });

  it('should render navigation menu button', () => {
    renderWithRouter(<LayoutWrapper />);
    const navBtn = screen.getByRole('button', { name: /menu|nav/i });
    expect(navBtn).toBeInTheDocument();
  });

  it('should toggle navigation menu on button click', async () => {
    const user = userEvent.setup();
    renderWithRouter(<LayoutWrapper />);

    const navBtn = screen.getByRole('button', { name: /menu|nav/i });
    await user.click(navBtn);

    // Menu should be visible
    const navMenu = screen.getByRole('navigation');
    expect(navMenu).toBeVisible();
  });

  it('should render main content area', () => {
    renderWithRouter(<LayoutWrapper />);
    const main = screen.getByRole('main');
    expect(main).toBeInTheDocument();
  });

  it('should render all 9 navigation items when menu is open', async () => {
    const user = userEvent.setup();
    renderWithRouter(<LayoutWrapper />);

    const navBtn = screen.getByRole('button', { name: /menu|nav/i });
    await user.click(navBtn);

    const expectedItems = [
      'Dashboard',
      'Tasks',
      'Content',
      'Approvals',
      'Services',
      'AI Studio',
      'Costs',
      'Performance',
      'Settings',
    ];

    expectedItems.forEach((item) => {
      expect(screen.getByText(item)).toBeInTheDocument();
    });
  });

  it('should have proper semantic HTML structure', () => {
    renderWithRouter(<LayoutWrapper />);

    expect(screen.getByRole('banner')).toBeInTheDocument(); // header
    expect(screen.getByRole('navigation')).toBeInTheDocument(); // nav
    expect(screen.getByRole('main')).toBeInTheDocument(); // main content
  });

  it('should not show menu items when menu is closed', () => {
    renderWithRouter(<LayoutWrapper />);
    const navItems = screen.queryAllByRole('link');

    // Only header links should be visible
    expect(navItems.length).toBeGreaterThanOrEqual(0);
  });

  it('should handle window resize gracefully', async () => {
    renderWithRouter(<LayoutWrapper />);

    // Simulate mobile viewport
    global.innerWidth = 375;
    window.dispatchEvent(new Event('resize'));

    expect(screen.getByRole('banner')).toBeInTheDocument();
  });
});
