/**
 * Error Boundary Component Tests (components/ErrorBoundary.js)
 *
 * Tests error boundary functionality
 * Verifies: Error catching, fallback UI, error logging
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import ErrorBoundary from '../components/ErrorBoundary';

// Mock console.error to avoid noise in test output
const consoleErrorSpy = jest
  .spyOn(console, 'error')
  .mockImplementation(() => {});

// Component that throws an error
const ThrowError = ({ shouldThrow = true }) => {
  if (shouldThrow) {
    throw new Error('Test error');
  }
  return <div>No error here</div>;
};

describe('ErrorBoundary Component', () => {
  beforeEach(() => {
    consoleErrorSpy.mockClear();
  });

  afterAll(() => {
    consoleErrorSpy.mockRestore();
  });

  it('should render children when there is no error', () => {
    render(
      <ErrorBoundary>
        <div>Safe content</div>
      </ErrorBoundary>
    );
    expect(screen.getByText('Safe content')).toBeInTheDocument();
  });

  it('should catch errors and render fallback UI', () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  });

  it('should display error fallback UI', () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );
    // ErrorBoundary shows user-friendly message, not raw error text
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  });

  it('should show reset button', () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );
    const resetButton = screen.queryByRole('button', {
      name: /try again|reload|reset|retry/i,
    });
    expect(resetButton).toBeInTheDocument();
  });

  it('should log error to console', () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );
    expect(consoleErrorSpy).toHaveBeenCalled();
  });

  it('should recover from error', () => {
    const { rerender } = render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );

    // First render should show error
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

    // Click reset button
    const resetButton = screen.queryByRole('button', {
      name: /try again|reload|reset|retry/i,
    });
    resetButton.click();

    // Rerender with safe component
    rerender(
      <ErrorBoundary>
        <ThrowError shouldThrow={false} />
      </ErrorBoundary>
    );

    expect(screen.getByText('No error here')).toBeInTheDocument();
  });

  it('should display error boundary heading', () => {
    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
  });

  it('should handle multiple children', () => {
    render(
      <ErrorBoundary>
        <div>First child</div>
        <div>Second child</div>
      </ErrorBoundary>
    );
    expect(screen.getByText('First child')).toBeInTheDocument();
    expect(screen.getByText('Second child')).toBeInTheDocument();
  });

  it('should show error fallback in development mode', () => {
    const originalEnv = process.env.NODE_ENV;
    process.env.NODE_ENV = 'development';

    render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );

    // Should still show the error boundary UI
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

    process.env.NODE_ENV = originalEnv;
  });

  it('should handle consecutive errors', () => {
    const { rerender } = render(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );

    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

    // Another error
    rerender(
      <ErrorBoundary>
        <ThrowError shouldThrow={true} />
      </ErrorBoundary>
    );

    // Should still show error UI
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  });
});
