/**
 * WebVitals Component Tests
 *
 * Tests the web vitals reporting component.
 * Verifies: Hook invocation, GA event dispatch, Sentry alert for poor vitals
 */

// Capture the callback passed to useReportWebVitals
let reportCallback;
jest.mock('next/web-vitals', () => ({
  useReportWebVitals: (cb) => {
    reportCallback = cb;
  },
}));

jest.mock('@sentry/nextjs', () => ({
  captureMessage: jest.fn(),
}));

import { render } from '@testing-library/react';
import WebVitals from '../WebVitals';

afterEach(() => {
  delete window.gtag;
  reportCallback = undefined;
});

describe('WebVitals Component', () => {
  it('should render nothing (return null)', () => {
    const { container } = render(<WebVitals />);
    expect(container.firstChild).toBeNull();
  });

  it('should register a callback with useReportWebVitals', () => {
    render(<WebVitals />);
    expect(typeof reportCallback).toBe('function');
  });

  it('should send good LCP metric to Google Analytics', () => {
    const gtagMock = jest.fn();
    window.gtag = gtagMock;

    render(<WebVitals />);
    reportCallback({ name: 'LCP', value: 2000, id: 'lcp-1' });

    expect(gtagMock).toHaveBeenCalledWith('event', 'LCP', {
      event_category: 'Web Vitals',
      event_label: 'lcp-1',
      value: 2000,
      non_interaction: true,
    });
  });

  it('should multiply CLS value by 1000 before sending to GA', () => {
    const gtagMock = jest.fn();
    window.gtag = gtagMock;

    render(<WebVitals />);
    reportCallback({ name: 'CLS', value: 0.15, id: 'cls-1' });

    expect(gtagMock).toHaveBeenCalledWith(
      'event',
      'CLS',
      expect.objectContaining({ value: 150 })
    );
  });

  it('should not call gtag when it is not available', () => {
    render(<WebVitals />);
    // Should not throw when gtag is missing
    expect(() =>
      reportCallback({ name: 'LCP', value: 2000, id: 'lcp-1' })
    ).not.toThrow();
  });

  it('should report to Sentry when a vital is rated poor', async () => {
    const Sentry = require('@sentry/nextjs');

    render(<WebVitals />);
    // LCP > 4000 = poor
    reportCallback({ name: 'LCP', value: 5000, id: 'lcp-poor' });

    // Wait for the dynamic import promise to resolve
    await new Promise((r) => setTimeout(r, 0));

    expect(Sentry.captureMessage).toHaveBeenCalledWith(
      'Web Vital degraded: LCP=5000ms',
      expect.objectContaining({
        level: 'warning',
        tags: { vital: 'LCP', rating: 'poor' },
      })
    );
  });

  it('should not report to Sentry when a vital is rated good', async () => {
    const Sentry = require('@sentry/nextjs');
    Sentry.captureMessage.mockClear();

    render(<WebVitals />);
    reportCallback({ name: 'LCP', value: 1000, id: 'lcp-good' });

    await new Promise((r) => setTimeout(r, 0));

    expect(Sentry.captureMessage).not.toHaveBeenCalled();
  });

  it('should not log to console.debug in non-development mode', () => {
    // NODE_ENV is 'test' in Jest, so the dev branch should NOT fire
    const spy = jest.spyOn(console, 'debug').mockImplementation(() => {});

    render(<WebVitals />);
    reportCallback({ name: 'FCP', value: 1500, id: 'fcp-1' });

    expect(spy).not.toHaveBeenCalled();

    spy.mockRestore();
  });
});
