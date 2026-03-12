import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Sidebar imports a CSS file which jsdom can't process — mock it
vi.mock('../common/Sidebar.css', () => ({}));

import Sidebar from '../common/Sidebar';

const renderSidebar = () =>
  render(
    <MemoryRouter>
      <Sidebar />
    </MemoryRouter>
  );

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the navigation element', () => {
    renderSidebar();
    expect(screen.getByRole('navigation')).toBeInTheDocument();
  });

  it('shows "Glad Labs" title by default (expanded state)', () => {
    renderSidebar();
    expect(screen.getByText('Glad Labs')).toBeInTheDocument();
  });

  it('renders all 9 nav links', () => {
    renderSidebar();
    const expectedLabels = [
      'Dashboard',
      'Tasks',
      'Models',
      'Social',
      'Content',
      'Workflows',
      'Costs',
      'Analytics',
      'Settings',
    ];
    expectedLabels.forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });
  });

  it('has correct href for Dashboard link', () => {
    renderSidebar();
    const dashboardLink = screen.getByRole('link', { name: /Dashboard/ });
    expect(dashboardLink).toHaveAttribute('href', '/');
  });

  it('has correct href for Tasks link', () => {
    renderSidebar();
    const tasksLink = screen.getByRole('link', { name: /Tasks/ });
    expect(tasksLink).toHaveAttribute('href', '/tasks');
  });

  it('has correct href for Settings link', () => {
    renderSidebar();
    const settingsLink = screen.getByRole('link', { name: /Settings/ });
    expect(settingsLink).toHaveAttribute('href', '/settings');
  });

  it('shows collapse button (←) when expanded', () => {
    renderSidebar();
    const toggleBtn = screen.getByLabelText('Collapse sidebar');
    expect(toggleBtn).toBeInTheDocument();
    expect(toggleBtn).toHaveTextContent('←');
  });

  it('toggles to compressed state when collapse button is clicked', () => {
    renderSidebar();
    const toggleBtn = screen.getByLabelText('Collapse sidebar');
    fireEvent.click(toggleBtn);

    // After collapse: title becomes 'GL' and button text changes to expand arrow
    expect(screen.getByText('GL')).toBeInTheDocument();
    expect(screen.queryByText('Glad Labs')).not.toBeInTheDocument();
  });

  it('shows expand button (→) after sidebar is collapsed', () => {
    renderSidebar();
    const collapseBtn = screen.getByLabelText('Collapse sidebar');
    fireEvent.click(collapseBtn);

    const expandBtn = screen.getByLabelText('Expand sidebar');
    expect(expandBtn).toBeInTheDocument();
    expect(expandBtn).toHaveTextContent('→');
  });

  it('restores expanded state after clicking expand', () => {
    renderSidebar();
    const collapseBtn = screen.getByLabelText('Collapse sidebar');
    fireEvent.click(collapseBtn);
    const expandBtn = screen.getByLabelText('Expand sidebar');
    fireEvent.click(expandBtn);

    expect(screen.getByText('Glad Labs')).toBeInTheDocument();
  });
});
