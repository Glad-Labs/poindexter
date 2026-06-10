/**
 * Newsletter Subscribe — Vercel Serverless Function
 *
 * Captures a signup in TWO durable places so there's no single point of
 * failure, then sends a welcome email:
 *   1. The backend `newsletter_subscribers` table — POST to
 *      `${NEXT_PUBLIC_API_BASE_URL}/api/newsletter/subscribe`, the Tailscale
 *      funnel that fronts the local FastAPI worker. This is the owned copy
 *      (handles dedup + unsubscribe tokens + email-enumeration protection).
 *   2. A Resend audience (for broadcast sends) when RESEND_AUDIENCE_ID is set.
 *
 * Success is returned ONLY if at least one durable store accepted the signup.
 * If both fail we return 503 so the failure is LOUD. The previous version
 * silently swallowed audience failures and returned success even when the
 * address was saved nowhere — quietly losing every real subscriber.
 *
 * Required Vercel env:
 *   - NEXT_PUBLIC_API_BASE_URL — backend funnel URL (e.g. https://<host>.ts.net)
 *   - RESEND_API_KEY           — must have Audiences permission for the audience write
 *   - RESEND_AUDIENCE_ID       — the Resend audience to add contacts to
 *
 * POST /api/newsletter/subscribe
 * Body: { email, first_name?, last_name?, company?, marketing_consent? }
 */

import * as Sentry from '@sentry/nextjs';
import { NextRequest, NextResponse } from 'next/server';
import { SITE_NAME, SITE_URL, NEWSLETTER_EMAIL } from '@/lib/site.config';

const RESEND_API_KEY = process.env.RESEND_API_KEY || '';
const RESEND_AUDIENCE_ID = process.env.RESEND_AUDIENCE_ID || '';
// Read the backend base URL directly (not via lib/url, which throws at module
// load when unset) so a missing var degrades gracefully instead of 500-ing the
// whole route — we still surface it loudly below.
const BACKEND_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  ''
).replace(/\/$/, '');

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { email, first_name, last_name, company, marketing_consent, interest_categories } = body;

    if (!email || !email.includes('@')) {
      return NextResponse.json(
        { success: false, detail: 'Valid email is required' },
        { status: 400 }
      );
    }

    // --- 1. Durable store: backend newsletter_subscribers table -------------
    let dbStored = false;
    if (BACKEND_BASE_URL) {
      try {
        const res = await fetch(`${BACKEND_BASE_URL}/api/newsletter/subscribe`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            email,
            first_name,
            last_name,
            company,
            marketing_consent,
            interest_categories,
          }),
        });
        dbStored = res.ok;
        if (!res.ok) {
          const detail = await res.text();
          const err = new Error(`[Newsletter] backend store failed: ${res.status} ${detail}`);
          Sentry.captureException(err);
          console.error(err.message);
        }
      } catch (err) {
        Sentry.captureException(err);
        console.error('[Newsletter] backend store error:', err);
      }
    } else {
      const err = new Error('[Newsletter] NEXT_PUBLIC_API_BASE_URL not set — cannot persist signup to backend DB');
      Sentry.captureException(err);
      console.error(err.message);
    }

    // --- 2. Resend audience (for broadcast sends) ---------------------------
    let audienceStored = false;
    if (RESEND_API_KEY && RESEND_AUDIENCE_ID) {
      const contact: Record<string, string | boolean> = {
        email,
        unsubscribed: false,
      };
      if (first_name) contact.first_name = first_name;
      if (last_name) contact.last_name = last_name;
      try {
        const res = await fetch(
          `https://api.resend.com/audiences/${RESEND_AUDIENCE_ID}/contacts`,
          {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${RESEND_API_KEY}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify(contact),
          }
        );
        audienceStored = res.ok;
        if (!res.ok) {
          console.error(
            '[Newsletter] Resend audience add failed:',
            res.status,
            await res.text()
          );
        }
      } catch (err) {
        console.error('[Newsletter] Resend audience error:', err);
      }
    }

    // --- Fail LOUD if the subscriber landed nowhere durable -----------------
    if (!dbStored && !audienceStored) {
      const captureErr = new Error(`[Newsletter] subscriber NOT captured in any durable store: ${email}`);
      Sentry.captureException(captureErr);
      console.error(captureErr.message);
      return NextResponse.json(
        {
          success: false,
          detail:
            'We could not save your subscription. Please try again shortly.',
        },
        { status: 503 }
      );
    }

    // --- 3. Welcome email (best-effort — the subscriber is already saved) ---
    if (RESEND_API_KEY) {
      try {
        const welcomeRes = await fetch('https://api.resend.com/emails', {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${RESEND_API_KEY}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            from: `${SITE_NAME} <${NEWSLETTER_EMAIL}>`,
            to: [email],
            subject: `Welcome to ${SITE_NAME}`,
            html: `
          <h2>Welcome to ${SITE_NAME}!</h2>
          <p>Thanks for subscribing${first_name ? `, ${first_name}` : ''}. You'll receive our latest articles on AI, hardware, and gaming delivered straight to your inbox.</p>
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
    }

    return NextResponse.json({
      success: true,
      message: 'Successfully subscribed!',
    });
  } catch (error) {
    Sentry.captureException(error);
    console.error('[Newsletter] Subscribe error:', error);
    return NextResponse.json(
      { success: false, detail: 'Internal server error' },
      { status: 500 }
    );
  }
}
