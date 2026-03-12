import React from 'react';
import { render, screen } from '@testing-library/react';
import StatusBadge from '../StatusBadge.jsx';

describe('StatusBadge', () => {
  it('renders "Completed" for status "completed"', () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });

  it('renders "Completed" for uppercase "COMPLETED"', () => {
    render(<StatusBadge status="COMPLETED" />);
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });

  it('renders "In Progress" for status "in_progress"', () => {
    render(<StatusBadge status="in_progress" />);
    expect(screen.getByText('In Progress')).toBeInTheDocument();
  });

  it('renders "Queued" for status "queued"', () => {
    render(<StatusBadge status="queued" />);
    expect(screen.getByText('Queued')).toBeInTheDocument();
  });

  it('renders "Failed" for status "failed"', () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('renders the raw status text for unknown statuses', () => {
    render(<StatusBadge status="pending" />);
    expect(screen.getByText('pending')).toBeInTheDocument();
  });

  it('renders the raw status text for custom statuses', () => {
    render(<StatusBadge status="custom_status" />);
    expect(screen.getByText('custom_status')).toBeInTheDocument();
  });

  it('applies base Tailwind classes to the span', () => {
    const { container } = render(<StatusBadge status="completed" />);
    const span = container.querySelector('span');
    expect(span.className).toContain('px-2');
    expect(span.className).toContain('py-1');
    expect(span.className).toContain('text-xs');
    expect(span.className).toContain('rounded-full');
  });

  it('applies green background classes for completed status', () => {
    const { container } = render(<StatusBadge status="completed" />);
    const span = container.querySelector('span');
    expect(span.className).toContain('bg-green-500');
  });

  it('applies yellow background classes for in_progress status', () => {
    const { container } = render(<StatusBadge status="in_progress" />);
    const span = container.querySelector('span');
    expect(span.className).toContain('bg-yellow-500');
  });

  it('applies blue background classes for queued status', () => {
    const { container } = render(<StatusBadge status="queued" />);
    const span = container.querySelector('span');
    expect(span.className).toContain('bg-blue-500');
  });

  it('applies red background classes for failed status', () => {
    const { container } = render(<StatusBadge status="failed" />);
    const span = container.querySelector('span');
    expect(span.className).toContain('bg-red-500');
  });

  it('applies gray background classes for unknown status', () => {
    const { container } = render(<StatusBadge status="unknown" />);
    const span = container.querySelector('span');
    expect(span.className).toContain('bg-gray-600');
  });
});
