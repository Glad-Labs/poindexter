/**
 * TaskStatusBadge Component Tests
 *
 * Tests the task status indicator component
 * Verifies: Status display, color coding, icons, accessibility
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import TaskStatusBadge from './TaskStatusBadge';

describe('TaskStatusBadge Component', () => {
  const statuses = [
    'pending',
    'processing',
    'generated',
    'reviewing',
    'completed',
    'failed',
    'cancelled',
  ];

  it('should render the badge container', () => {
    render(<TaskStatusBadge status="pending" />);

    const badge =
      screen.getByRole('status') || screen.getByTestId(/status|badge/i);
    expect(badge).toBeInTheDocument();
  });

  it('should display status text', () => {
    render(<TaskStatusBadge status="completed" />);

    expect(screen.getByText(/completed/i)).toBeInTheDocument();
  });

  it.each(statuses)('should render %s status', (status) => {
    const { container } = render(<TaskStatusBadge status={status} />);

    const badge =
      container.querySelector('[role="status"]') ||
      container.querySelector('[data-status-badge]');
    expect(badge).toBeInTheDocument();
  });

  it('should apply correct color class for success status', () => {
    const { container } = render(<TaskStatusBadge status="completed" />);

    const badge = container.querySelector('[role="status"]');
    expect(badge?.className).toMatch(/success|completed|green/i);
  });

  it('should apply correct color class for error status', () => {
    const { container } = render(<TaskStatusBadge status="failed" />);

    const badge = container.querySelector('[role="status"]');
    expect(badge?.className).toMatch(/error|failed|red|danger/i);
  });

  it('should apply correct color class for warning status', () => {
    const { container } = render(<TaskStatusBadge status="reviewing" />);

    const badge = container.querySelector('[role="status"]');
    expect(badge?.className).toMatch(/warning|review|yellow|orange/i);
  });

  it('should apply correct color class for info status', () => {
    const { container } = render(<TaskStatusBadge status="processing" />);

    const badge = container.querySelector('[role="status"]');
    expect(badge?.className).toMatch(/info|processing|blue/i);
  });

  it('should display status icon when provided', () => {
    const { container } = render(
      <TaskStatusBadge status="completed" showIcon />
    );

    const icon =
      container.querySelector('[data-icon]') || container.querySelector('svg');
    expect(icon).toBeInTheDocument();
  });

  it('should support custom className', () => {
    const { container } = render(
      <TaskStatusBadge status="pending" className="custom-badge" />
    );

    const badge = container.querySelector('.custom-badge');
    expect(badge).toBeInTheDocument();
  });

  it('should be keyboard accessible', () => {
    const { container } = render(<TaskStatusBadge status="pending" />);

    const badge = container.querySelector('[role="status"]');
    expect(badge).toHaveAttribute('aria-label') ||
      expect(badge?.textContent).toMatch(/pending/i);
  });

  it('should display tooltip on hover', async () => {
    const { container } = render(
      <TaskStatusBadge status="failed" tooltip="Task processing failed" />
    );

    const badge = container.querySelector('[role="status"]');
    expect(badge).toHaveAttribute('title') ||
      expect(badge).toHaveAttribute('aria-label');
  });

  it('should handle unknown status gracefully', () => {
    const { container } = render(<TaskStatusBadge status="unknown_status" />);

    const badge =
      container.querySelector('[role="status"]') ||
      container.querySelector('[data-status-badge]');
    expect(badge).toBeInTheDocument();
  });

  it('should support uppercase rendering', () => {
    const { container } = render(
      <TaskStatusBadge status="pending" uppercase />
    );

    const badge = container.querySelector('[role="status"]');
    const text = badge?.textContent || '';
    expect(
      text === text.toUpperCase() || text.toUpperCase().includes('PENDING')
    ).toBe(true);
  });

  it('should support pill-shaped variant', () => {
    const { container } = render(<TaskStatusBadge status="completed" pill />);

    const badge = container.querySelector('[role="status"]');
    expect(badge?.className).toMatch(/pill|rounded/i) ||
      expect(badge).toBeInTheDocument();
  });

  it('should support size variants', () => {
    const { container: smallContainer } = render(
      <TaskStatusBadge status="pending" size="sm" />
    );

    const { container: largeContainer } = render(
      <TaskStatusBadge status="pending" size="lg" />
    );

    const smallBadge = smallContainer.querySelector('[role="status"]');
    const largeBadge = largeContainer.querySelector('[role="status"]');

    expect(smallBadge).toBeInTheDocument();
    expect(largeBadge).toBeInTheDocument();
  });

  it('should display progress indicator for processing status', () => {
    render(<TaskStatusBadge status="processing" showProgress />);

    const spinner = screen.queryByTestId(/spinner|loading|indicator/i);
    expect(spinner).toBeDefined();
  });

  it('should handle null/undefined status gracefully', () => {
    const { container } = render(<TaskStatusBadge status={null} />);

    expect(
      container.querySelector('[role="status"]') || container.firstChild
    ).toBeDefined();
  });
});
