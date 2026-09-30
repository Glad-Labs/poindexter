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
export const POINDEXTER_VERSION = '0.151.1'; // x-release-please-version

// Lemon Squeezy subscription product URL for Poindexter Pro.
// Single tier: $19/month or $180/year (7-day trial applies once CHECKOUT_LIVE).
// The product is PUBLISHED and this URL resolves to a live checkout cart
// (verified 2026-09-23). It read "currently UNPUBLISHED / checkout gated"
// for a month after CHECKOUT_LIVE went true on 2026-08-26 — see the block
// below, which was right while this one contradicted it.
export const LS_PRO_URL =
  'https://gladlabs.lemonsqueezy.com/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9';

// Prices shown on the site. Lemon Squeezy controls the actual charged price;
// these are copy only. Keep them in sync manually.
// Founding Member rate — locked for life; the standard rate rises after launch.
//
// WHAT Pro contains is described in marketing/pro-offer.md — the one canonical
// description, with the list of every surface (README, SUPPORT.md, docs, these
// pages, the FAQ) that paraphrases it. Change the offer there first.
export const PRO_MONTHLY_USD = 19;
export const PRO_ANNUAL_USD = 180;
export const PRO_TRIAL_DAYS = 7;

// Seed size, stated as a floor. The weekly pro-freshness rebuild writes the
// exact count into the Pro repo's config/README.md (1,228 on 2026-09-20). This
// read "1,800+" while the build shipped 1,228 — a floor that overstates is a
// false claim on a paid product, so lower it the week a rebuild dips below.
export const PRO_SEED_KEYS_FLOOR = '1,200+';

// LIVE since 2026-08-26: the pay→deliver chain shipped (glad-labs-stack#3216 —
// LS poll → GitHub collaborator invite, weekly freshness rebuilds) and a live
// test purchase delivered end-to-end (sub 2470345). While false, every Pro CTA
// pointed to the founding-members community instead of checkout, so no one
// could be charged for a deliverable that couldn't yet be delivered.
export const CHECKOUT_LIVE = true;

// The field guide — an e-book for developers directing AI coding agents on a
// real codebase. Decided 2026-09-27: a Pro perk, not a separate product. Pro
// subscribers get it in the Pro repo when it ships; everyone else can join a
// free waitlist, a $0 Lemon Squeezy "notify me" product. Plan + turn-on
// checklist: marketing/field-guide/README.md.
//
// OFF by default: /field-guide 404s and the nav hides it until the flag is
// true AND the page has something to act on (the waitlist URL before the
// guide ships, Pro itself after), so flipping the flag alone can never
// publish a page with a dead button.
export const FIELD_GUIDE_LIVE = false;
export const LS_FIELD_GUIDE_WAITLIST_URL = '';
// Flip only once the guide is actually in the Pro repo. Until then every
// surface says "coming to Pro", never "included" (marketing/pro-offer.md).
export const FIELD_GUIDE_SHIPPED = false;
// Optional: a free sample chapter published elsewhere (e.g. on the dev diary).
export const FIELD_GUIDE_SAMPLE_URL = '';
export const FIELD_GUIDE_ENABLED =
  FIELD_GUIDE_LIVE &&
  (FIELD_GUIDE_SHIPPED || LS_FIELD_GUIDE_WAITLIST_URL !== '');

// Founding-members CTA (used while CHECKOUT_LIVE === false).
// Permanent invite (Expire: Never) minted 2026-08-26 — the previous one was
// created with Discord's default 7-day expiry and died silently, so the CTA
// dead-ended for weeks. If this is ever reminted, verify it resolves:
//   curl -s https://discord.com/api/v10/invites/<code> | grep guild
export const FOUNDING_CTA_URL = 'https://discord.gg/M6vZvAeQVn';
export const FOUNDING_CTA_LABEL = 'Join the founding members';
