/**
 * Newsletter Subscribe — Vercel Serverless Function
 *
 * Captures a signup as a contact in the Resend segment the backend syncs,
 * then sends a welcome email.
 *
 * Why Resend and not the backend: the backend is local-first with no public
 * ingress, so this function cannot reach it. This route used to POST each
 * signup to the backend through a Tailscale Funnel hostname. That hostname
 * belonged to a node that was later retired; it stopped resolving, and every
 * signup failed that leg without anyone noticing. The backend now pulls the
 * segment into its own `newsletter_subscribers` table every 15 minutes
 * (SyncNewsletterAudienceJob), outbound-only. A daily canary
 * (ProbeNewsletterSignupJob) signs a Resend test inbox up through THIS route
 * and checks the backend can see it, so the path cannot go dark silently
 * again.
 *
 * `POST /contacts` is an upsert (verified against the live API 2026-09-28):
 * a returning subscriber is re-consented (`unsubscribed: false`) and re-added
 * to the segment rather than rejected.
 *
 * Success is returned ONLY when Resend accepted the contact; otherwise 503.
 * A visitor is never told they subscribed when nothing was saved. The
 * pre-2026-06 route did exactly that, and it cost every early subscriber.
 *
 * Required Vercel env:
 *   - RESEND_API_KEY     — a key with contacts write + sending access
 *   - RESEND_AUDIENCE_ID — the Resend segment id. It MUST equal the backend's
 *                          `resend_audience_id` setting, or the backend never
 *                          sees the signup (the canary checks exactly this)
 *
 * Only email and first/last name are kept: a Resend contact carries nothing
 * else, and nothing downstream reads company, interests or consent flags.
 *
 * POST /api/newsletter/subscribe
 * Body: { email, first_name?, last_name? }  (other fields are ignored)
 */

/* eslint-disable no-console */
import * as Sentry from '@sentry/nextjs';
import { NextRequest, NextResponse } from 'next/server';
import { SITE_NAME, SITE_URL, NEWSLETTER_EMAIL } from '@/lib/site.config';

const RESEND_API = 'https://api.resend.com';
// Loose on purpose: Resend validates the address. This only rejects values
// that are not an address at all before spending a Resend call on them.
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const EMAIL_MAX = 255;
const NAME_MAX = 100;

const NOT_SAVED =
  'We could not save your subscription. Please try again shortly.';

function clipName(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim().slice(0, NAME_MAX);
  return trimmed || undefined;
}

// The welcome email goes to whatever address was typed into a public form.
// An unescaped name would let anyone put their own markup (a link, say) into
// mail sent from our domain to an address of their choosing.
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Never put the address in these messages: they go to Sentry and the logs.
function reportFailure(message: string, cause?: unknown) {
  Sentry.captureException(cause ?? new Error(message));
  console.error(message, cause ?? '');
}

export async function POST(request: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { success: false, detail: 'Valid email is required' },
      { status: 400 }
    );
  }

  const email = typeof body?.email === 'string' ? body.email.trim() : '';
  if (!email || email.length > EMAIL_MAX || !EMAIL_RE.test(email)) {
    return NextResponse.json(
      { success: false, detail: 'Valid email is required' },
      { status: 400 }
    );
  }
  const firstName = clipName(body.first_name);
  const lastName = clipName(body.last_name);

  // Read per request: a missing variable fails this request loudly instead of
  // failing module load for the whole route.
  const apiKey = process.env.RESEND_API_KEY || '';
  const segmentId = process.env.RESEND_AUDIENCE_ID || '';
  if (!apiKey || !segmentId) {
    reportFailure(
      '[Newsletter] RESEND_API_KEY / RESEND_AUDIENCE_ID not set — signup NOT captured'
    );
    return NextResponse.json(
      { success: false, detail: NOT_SAVED },
      { status: 503 }
    );
  }

  // --- Capture: upsert the contact into the segment ------------------------
  const contact: Record<string, unknown> = {
    email,
    unsubscribed: false,
    segments: [{ id: segmentId }],
  };
  if (firstName) contact.first_name = firstName;
  if (lastName) contact.last_name = lastName;

  let captured = false;
  try {
    const res = await fetch(`${RESEND_API}/contacts`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(contact),
    });
    captured = res.ok;
    if (!res.ok) {
      reportFailure(
        `[Newsletter] Resend contact upsert failed: ${res.status} ${await res.text()} — signup NOT captured`
      );
    }
  } catch (err) {
    reportFailure(
      '[Newsletter] Resend contact upsert error — signup NOT captured',
      err
    );
  }

  if (!captured) {
    return NextResponse.json(
      { success: false, detail: NOT_SAVED },
      { status: 503 }
    );
  }

  // --- Welcome email (best-effort — the subscriber is already saved) ------
  try {
    const greeting = firstName ? `, ${escapeHtml(firstName)}` : '';
    const welcomeRes = await fetch(`${RESEND_API}/emails`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: `${SITE_NAME} <${NEWSLETTER_EMAIL}>`,
        to: [email],
        subject: `Welcome to ${SITE_NAME}`,
        html: `
          <h2>Welcome to ${SITE_NAME}!</h2>
          <p>Thanks for subscribing${greeting}. You'll receive our latest articles on AI, hardware, and gaming delivered straight to your inbox.</p>
          <p>In the meantime, check out our latest posts at <a href="${SITE_URL}">${SITE_URL.replace('https://', '')}</a>.</p>
          <p style="color: #666; font-size: 12px; margin-top: 32px;">
            You can unsubscribe at any time by replying to this email.
          </p>
        `,
      }),
    });
    if (!welcomeRes.ok) {
      console.error(
        '[Newsletter] welcome email failed:',
        welcomeRes.status,
        await welcomeRes.text()
      );
    }
  } catch (err) {
    console.error('[Newsletter] welcome email error:', err);
  }

  return NextResponse.json({
    success: true,
    message: 'Successfully subscribed!',
  });
}
