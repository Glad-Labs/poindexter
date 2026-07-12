import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('@sentry/nextjs', () => ({
  captureException: jest.fn(),
}));

const mockReferrals = [
  {
    code: 'mercury',
    name: 'Mercury',
    description: 'Business banking we use daily.',
    category: 'service',
  },
  {
    code: 'prime-gaming',
    name: 'Prime Gaming',
    description: 'Free games every month with Prime.',
    category: 'product',
  },
];

beforeEach(() => {
  global.fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve(mockReferrals),
    })
  );
});

afterEach(() => {
  jest.restoreAllMocks();
});

let ReferralsPage;
beforeAll(async () => {
  const mod = await import('../page');
  ReferralsPage = mod.default;
});

describe('Referrals Page (/referrals)', () => {
  const renderPage = async () => {
    const jsx = await ReferralsPage();
    return render(jsx);
  };

  test('renders page heading', async () => {
    await renderPage();
    const headings = screen.getAllByText(/referrals/i);
    expect(headings.length).toBeGreaterThan(0);
  });

  test('renders service and product section headings', async () => {
    await renderPage();
    expect(screen.getByText('Tools & Services')).toBeInTheDocument();
    expect(screen.getByText('Amazon Picks')).toBeInTheDocument();
  });

  test('renders each referral name and description', async () => {
    await renderPage();
    expect(screen.getByText('Mercury')).toBeInTheDocument();
    expect(
      screen.getByText('Business banking we use daily.')
    ).toBeInTheDocument();
    expect(screen.getByText('Prime Gaming')).toBeInTheDocument();
  });

  test('CTA links point at /go/<code>', async () => {
    await renderPage();
    const ctaLinks = screen
      .getAllByRole('link')
      .filter((a) => a.getAttribute('href')?.startsWith('/go/'));
    expect(ctaLinks.length).toBe(2);
    expect(ctaLinks.some((a) => a.getAttribute('href') === '/go/mercury')).toBe(
      true
    );
  });

  test('renders the affiliate disclosure', async () => {
    await renderPage();
    expect(
      screen.getByRole('note', { name: 'Affiliate disclosure' })
    ).toBeInTheDocument();
  });

  test('renders empty state when no referrals', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve([]) })
    );
    await renderPage();
    expect(screen.getByText(/nothing published here yet/i)).toBeInTheDocument();
  });

  test('handles fetch failure gracefully', async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error('Network error')));
    await renderPage();
    expect(screen.getByText(/nothing published here yet/i)).toBeInTheDocument();
  });
});
