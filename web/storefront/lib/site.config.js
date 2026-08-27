/*
  Storefront-wide constants. Kept in one file so swapping the real Lemon
  Squeezy product URL — or flipping the gated-launch switch — is a one-line diff.
*/

export const SITE_NAME = 'Glad Labs';
export const SITE_URL = 'https://gladlabs.ai';

// Poindexter product version shown in the hero eyebrow. This is the single
// source of truth for the storefront — release-please bumps the literal below
// on every release (see the `generic` extra-files entry in
// release-please-config.json), so it tracks the real version instead of
// drifting. Keep the `// x-release-please-version` annotation on this line.
export const POINDEXTER_VERSION = '0.129.0'; // x-release-please-version

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

// LIVE since 2026-08-26: the pay→deliver chain shipped (glad-labs-stack#3216 —
// LS poll → GitHub collaborator invite, weekly freshness rebuilds) and a live
// test purchase delivered end-to-end (sub 2470345). While false, every Pro CTA
// pointed to the founding-members community instead of checkout, so no one
// could be charged for a deliverable that couldn't yet be delivered.
export const CHECKOUT_LIVE = true;

// Founding-members CTA (used while CHECKOUT_LIVE === false).
// Permanent invite (Expire: Never) minted 2026-08-26 — the previous one was
// created with Discord's default 7-day expiry and died silently, so the CTA
// dead-ended for weeks. If this is ever reminted, verify it resolves:
//   curl -s https://discord.com/api/v10/invites/<code> | grep guild
export const FOUNDING_CTA_URL = 'https://discord.gg/M6vZvAeQVn';
export const FOUNDING_CTA_LABEL = 'Join the founding members';
