/**
 * Site Configuration — single source of truth for brand values in the frontend.
 *
 * All brand-specific strings (name, URL, emails, taglines) live here.
 * Values come from NEXT_PUBLIC_* environment variables with sensible defaults;
 * .env.example describes each one.
 *
 * To customize for a different site, set the env vars — no code changes needed.
 *
 * Export only what something imports. SITE_DOMAIN, SITE_DESCRIPTION and
 * VIDEO_FEED_NAME were removed on 2026-09-28: nothing had ever imported them,
 * so their variables looked like settings and changed nothing.
 */

export const SITE_NAME = process.env.NEXT_PUBLIC_SITE_NAME || 'Glad Labs';
export const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';
export const SITE_TAGLINE =
  process.env.NEXT_PUBLIC_SITE_TAGLINE ||
  'AI, Hardware & the Edges Where They Meet';

export const COMPANY_NAME =
  process.env.NEXT_PUBLIC_COMPANY_NAME || 'Glad Labs, LLC';
export const SUPPORT_EMAIL =
  process.env.NEXT_PUBLIC_SUPPORT_EMAIL || 'hello@gladlabs.io';
export const PRIVACY_EMAIL =
  process.env.NEXT_PUBLIC_PRIVACY_EMAIL || 'privacy@gladlabs.io';
export const OWNER_EMAIL =
  process.env.NEXT_PUBLIC_OWNER_EMAIL || 'hello@gladlabs.io';
export const NEWSLETTER_EMAIL =
  process.env.NEXT_PUBLIC_NEWSLETTER_EMAIL || 'newsletter@gladlabs.io';

export const PODCAST_NAME =
  process.env.NEXT_PUBLIC_PODCAST_NAME || 'Glad Labs Podcast';

// Google AdSense — publisher ID + the in-content ad slot used at the bottom of
// each post. Both come from NEXT_PUBLIC_* env so a fork can drop in its own
// account without code changes. ADSENSE_ID defaults to the Glad Labs account
// (ca-pub-4578747062758519). ADSENSE_SLOT_ID has no default: AdSense is pending
// approval, so there is no real slot yet — an empty slot makes AdUnit render
// nothing (no fabricated slot ID). When approval lands, create an in-article
// unit in the AdSense dashboard and set NEXT_PUBLIC_ADSENSE_SLOT_ID to its slot
// ID; the bottom-of-post <ins> then renders and the consent-gated loader
// (CookieConsentBanner) fills it.
export const ADSENSE_ID =
  process.env.NEXT_PUBLIC_ADSENSE_ID || 'ca-pub-4578747062758519';
export const ADSENSE_SLOT_ID = process.env.NEXT_PUBLIC_ADSENSE_SLOT_ID || '';
