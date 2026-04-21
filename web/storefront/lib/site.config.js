/*
  Storefront-wide constants. Kept in one file so swapping the real Lemon
  Squeezy product URL is a one-line diff.
*/

export const SITE_NAME = 'Glad Labs';
export const SITE_URL = 'https://gladlabs.ai';

// PLACEHOLDER — replace with the real Lemon Squeezy product URL when the
// Quick Start Guide is live. Format: https://<store>.lemonsqueezy.com/buy/<id>
// until then the overlay opens to a 404 on lemonsqueezy.com, which is the
// least bad fallback (clearer than a silent no-op).
export const LS_GUIDE_URL =
  'https://gladlabs.lemonsqueezy.com/buy/REPLACE_WITH_REAL_PRODUCT_ID';

// Price shown on the site. Lemon Squeezy controls the actual charged price;
// this is copy only. Keep them in sync manually.
export const GUIDE_PRICE_USD = 29;
