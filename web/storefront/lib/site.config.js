/*
  Storefront-wide constants. Kept in one file so swapping the real Lemon
  Squeezy product URL — or flipping the gated-launch switch — is a one-line diff.
*/

export const SITE_NAME = 'Glad Labs';
export const SITE_URL = 'https://gladlabs.ai';

// Lemon Squeezy subscription product URL for Poindexter Pro.
// Single tier: $19/month or $180/year (7-day trial applies once CHECKOUT_LIVE).
// NOTE: the LS product is currently UNPUBLISHED and checkout is gated (see
// CHECKOUT_LIVE) until the Pro delivery channel ships. Re-publish the product
// and flip CHECKOUT_LIVE to turn billing back on.
export const LS_PRO_URL =
  'https://gladlabs.lemonsqueezy.com/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9';

// Prices shown on the site. Lemon Squeezy controls the actual charged price;
// these are copy only. Keep them in sync manually.
// Founding Member rate — locked for life; the standard rate rises after launch.
export const PRO_MONTHLY_USD = 19;
export const PRO_ANNUAL_USD = 180;
export const PRO_TRIAL_DAYS = 7;

// Gated launch. While false, every Pro CTA points to the founding-members
// community instead of opening the Lemon Squeezy checkout — so no one is
// charged for a deliverable that can't yet be delivered. Flip to true once the
// Pro delivery channel exists and a live test purchase has been verified.
export const CHECKOUT_LIVE = false;

// Founding-members CTA (used while CHECKOUT_LIVE === false).
export const FOUNDING_CTA_URL = 'https://discord.gg/GCDBxBVv';
export const FOUNDING_CTA_LABEL = 'Join the founding members';
