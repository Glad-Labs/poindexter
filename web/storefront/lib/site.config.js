/*
  Storefront-wide constants. Kept in one file so swapping the real Lemon
  Squeezy product URL is a one-line diff.
*/

export const SITE_NAME = 'Glad Labs';
export const SITE_URL = 'https://gladlabs.ai';

// Lemon Squeezy subscription product URL for Poindexter Pro.
// Single tier: $9/month or $89/year, 7-day free trial.
export const LS_PRO_URL =
  'https://gladlabs.lemonsqueezy.com/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9';

// Prices shown on the site. Lemon Squeezy controls the actual charged price;
// these are copy only. Keep them in sync manually.
export const PRO_MONTHLY_USD = 9;
export const PRO_ANNUAL_USD = 89;
export const PRO_TRIAL_DAYS = 7;
