/**
 * Site Configuration — single source of truth for brand values in the frontend.
 *
 * All brand-specific strings (name, URL, emails, taglines) live here.
 * Values come from NEXT_PUBLIC_* environment variables with sensible defaults.
 *
 * To customize for a different site, set the env vars — no code changes needed.
 */

export const SITE_NAME = process.env.NEXT_PUBLIC_SITE_NAME || 'Glad Labs';
export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://www.gladlabs.io';
export const SITE_DOMAIN = process.env.NEXT_PUBLIC_SITE_DOMAIN || 'gladlabs.io';
export const SITE_TAGLINE = process.env.NEXT_PUBLIC_SITE_TAGLINE || 'Technology & Innovation';
export const SITE_DESCRIPTION = process.env.NEXT_PUBLIC_SITE_DESCRIPTION || 'One person + AI = unlimited scale';

export const COMPANY_NAME = process.env.NEXT_PUBLIC_COMPANY_NAME || 'Glad Labs, LLC';
export const SUPPORT_EMAIL = process.env.NEXT_PUBLIC_SUPPORT_EMAIL || 'hello@gladlabs.io';
export const PRIVACY_EMAIL = process.env.NEXT_PUBLIC_PRIVACY_EMAIL || 'privacy@gladlabs.io';
export const OWNER_EMAIL = process.env.NEXT_PUBLIC_OWNER_EMAIL || 'matt@gladlabs.io';
export const NEWSLETTER_EMAIL = process.env.NEXT_PUBLIC_NEWSLETTER_EMAIL || 'newsletter@gladlabs.io';

export const PODCAST_NAME = process.env.NEXT_PUBLIC_PODCAST_NAME || 'Glad Labs Podcast';
export const VIDEO_FEED_NAME = process.env.NEXT_PUBLIC_VIDEO_FEED_NAME || 'Glad Labs Video';
