/**
 * @jest-environment node
 *
 * Tests for /api/newsletter/subscribe — the public signup capture.
 *
 * The route captures a signup as a contact in the Resend segment that the
 * backend pulls into newsletter_subscribers (SyncNewsletterAudienceJob). What
 * these pin:
 *   - the capture is a Resend `POST /contacts` into RESEND_AUDIENCE_ID, and
 *     nothing is sent anywhere else (the old backend-funnel leg is gone);
 *   - success is reported ONLY when Resend accepted the contact, otherwise 503;
 *   - the welcome email is best-effort and escapes the visitor-supplied name.
 *
 * Mocks global.fetch so no real HTTP calls are made.
 */

import { NextRequest } from 'next/server';
import { POST } from '../../app/api/newsletter/subscribe/route';

jest.mock('@sentry/nextjs', () => ({ captureException: jest.fn() }));

const SEGMENT = '33333333-aaaa-4bbb-8ccc-dddddddddddd';

type FetchCall = { url: string; init: RequestInit };

function makeRequest(body: unknown): NextRequest {
  return new NextRequest('http://localhost/api/newsletter/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

/** Route every fetch by URL; returns the recorded calls. */
function mockFetch(handlers: {
  contacts?: () => Promise<Response> | Response;
  emails?: () => Promise<Response> | Response;
}): FetchCall[] {
  const calls: FetchCall[] = [];
  global.fetch = jest.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init: init ?? {} });
      if (url === 'https://api.resend.com/contacts' && handlers.contacts) {
        return handlers.contacts();
      }
      if (url === 'https://api.resend.com/emails' && handlers.emails) {
        return handlers.emails();
      }
      throw new Error(`unexpected fetch ${url}`);
    }
  ) as unknown as typeof fetch;
  return calls;
}

const ok = (status = 201) =>
  new Response(JSON.stringify({ object: 'contact', id: 'c1' }), { status });

describe('POST /api/newsletter/subscribe', () => {
  const saved = { ...process.env };

  beforeEach(() => {
    process.env.RESEND_API_KEY = 're_test';
    process.env.RESEND_AUDIENCE_ID = SEGMENT;
    process.env.NEXT_PUBLIC_API_BASE_URL = 'https://backend.example';
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    process.env = { ...saved };
    jest.restoreAllMocks();
  });

  it('upserts the contact into the segment and reports success', async () => {
    const calls = mockFetch({ contacts: () => ok(), emails: () => ok(200) });
    const res = await POST(
      makeRequest({ email: 'reader@example.com', first_name: 'Ada' })
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      success: true,
      message: 'Successfully subscribed!',
    });

    const capture = calls[0];
    expect(capture.url).toBe('https://api.resend.com/contacts');
    expect(capture.init.method).toBe('POST');
    expect(JSON.parse(String(capture.init.body))).toEqual({
      email: 'reader@example.com',
      unsubscribed: false,
      segments: [{ id: SEGMENT }],
      first_name: 'Ada',
    });
    expect((capture.init.headers as Record<string, string>).Authorization).toBe(
      'Bearer re_test'
    );
  });

  it('never calls the backend: the site cannot reach it', async () => {
    const calls = mockFetch({ contacts: () => ok(), emails: () => ok(200) });
    await POST(makeRequest({ email: 'reader@example.com' }));
    expect(calls.map((c) => c.url)).toEqual([
      'https://api.resend.com/contacts',
      'https://api.resend.com/emails',
    ]);
    expect(calls.some((c) => c.url.includes('backend.example'))).toBe(false);
  });

  it('keeps only email and names: nothing downstream reads the rest', async () => {
    const calls = mockFetch({ contacts: () => ok(), emails: () => ok(200) });
    await POST(
      makeRequest({
        email: '  reader@example.com ',
        company: 'Acme',
        interest_categories: ['AI'],
        marketing_consent: true,
      })
    );
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      email: 'reader@example.com',
      unsubscribed: false,
      segments: [{ id: SEGMENT }],
    });
  });

  it.each([
    ['a rejected contact', () => new Response('forbidden', { status: 403 })],
    ['a network failure', () => Promise.reject(new TypeError('fetch failed'))],
  ])('returns 503 and sends no welcome email on %s', async (_label, fail) => {
    const calls = mockFetch({ contacts: fail, emails: () => ok(200) });
    const res = await POST(makeRequest({ email: 'reader@example.com' }));
    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.success).toBe(false);
    expect(body.detail).toMatch(/could not save/i);
    expect(calls.some((c) => c.url.endsWith('/emails'))).toBe(false);
  });

  it.each([['RESEND_API_KEY'], ['RESEND_AUDIENCE_ID']])(
    'returns 503 without calling Resend when %s is missing',
    async (name) => {
      delete process.env[name];
      const calls = mockFetch({ contacts: () => ok() });
      const res = await POST(makeRequest({ email: 'reader@example.com' }));
      expect(res.status).toBe(503);
      expect(calls).toEqual([]);
    }
  );

  it('keeps the address out of error reports', async () => {
    const Sentry = jest.requireMock('@sentry/nextjs');
    mockFetch({ contacts: () => new Response('nope', { status: 422 }) });
    await POST(makeRequest({ email: 'private@example.com' }));
    const reported = JSON.stringify(Sentry.captureException.mock.calls);
    const logged = JSON.stringify(
      (console.error as jest.Mock).mock.calls.map((args) => args.map(String))
    );
    expect(reported).not.toContain('private@example.com');
    expect(logged).not.toContain('private@example.com');
  });

  it.each([
    [{}],
    [{ email: 'not-an-address' }],
    [{ email: 42 }],
    [{ email: `${'x'.repeat(250)}@example.com` }],
    ['{not json'],
  ])('rejects %p with 400 before calling Resend', async (body) => {
    const calls = mockFetch({});
    const res = await POST(makeRequest(body));
    expect(res.status).toBe(400);
    expect(calls).toEqual([]);
  });

  it('still succeeds when the welcome email fails', async () => {
    mockFetch({
      contacts: () => ok(),
      emails: () => new Response('boom', { status: 500 }),
    });
    const res = await POST(makeRequest({ email: 'reader@example.com' }));
    expect(res.status).toBe(200);
  });

  it('escapes the visitor-supplied name in the welcome email', async () => {
    const calls = mockFetch({ contacts: () => ok(), emails: () => ok(200) });
    await POST(
      makeRequest({
        email: 'victim@example.com',
        first_name: '<a href="https://evil.example">Verify your account</a>',
      })
    );
    const welcome = JSON.parse(String(calls[1].init.body));
    expect(welcome.to).toEqual(['victim@example.com']);
    expect(welcome.html).not.toContain('<a href="https://evil.example">');
    expect(welcome.html).toContain('&lt;a href=&quot;https://evil.example');
  });
});
