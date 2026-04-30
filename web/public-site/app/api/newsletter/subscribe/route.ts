/**
 * Newsletter Subscribe — Vercel Serverless Function
 *
 * Handles newsletter subscriptions directly via Resend API.
 * No backend dependency — runs entirely on Vercel Edge.
 *
 * POST /api/newsletter/subscribe
 * Body: { email, first_name?, last_name?, company?, marketing_consent? }
 */

import { NextRequest, NextResponse } from 'next/server';
import { SITE_NAME, SITE_URL, NEWSLETTER_EMAIL } from '@/lib/site.config';

const RESEND_API_KEY = process.env.RESEND_API_KEY || '';
const RESEND_AUDIENCE_ID = process.env.RESEND_AUDIENCE_ID || '';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { email, first_name, last_name } = body;

    if (!email || !email.includes('@')) {
      return NextResponse.json(
        { success: false, detail: 'Valid email is required' },
        { status: 400 }
      );
    }

    if (!RESEND_API_KEY) {
      console.error('[Newsletter] RESEND_API_KEY not configured');
      return NextResponse.json(
        { success: false, detail: 'Newsletter service not configured' },
        { status: 503 }
      );
    }

    // Add contact to Resend audience
    const resendPayload: Record<string, string> = { email };
    if (first_name) resendPayload.first_name = first_name;
    if (last_name) resendPayload.last_name = last_name;

    if (RESEND_AUDIENCE_ID) {
      // Add to audience (contact list)
      const audienceRes = await fetch(
        `https://api.resend.com/audiences/${RESEND_AUDIENCE_ID}/contacts`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${RESEND_API_KEY}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(resendPayload),
        }
      );

      if (!audienceRes.ok) {
        const err = await audienceRes.text();
        console.error('[Newsletter] Resend audience error:', err);
        // Don't fail — still send welcome email
      }
    }

    // Send welcome email
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
      const err = await welcomeRes.text();
      console.error('[Newsletter] Resend email error:', err);
      return NextResponse.json(
        { success: false, detail: 'Failed to send welcome email' },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message: 'Successfully subscribed!',
    });
  } catch (error) {
    console.error('[Newsletter] Subscribe error:', error);
    return NextResponse.json(
      { success: false, detail: 'Internal server error' },
      { status: 500 }
    );
  }
}
