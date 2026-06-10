'use client';

/*
  <ProCTA> — the single Pro call-to-action for the storefront.

  Gated launch: while site.config `CHECKOUT_LIVE` is false, every Pro CTA points
  to the founding-members community (no charge) instead of opening the Lemon
  Squeezy checkout. When CHECKOUT_LIVE flips true, the same component renders the
  LemonSqueezyOverlay trial button — callers don't change. This keeps the
  live/gated decision in exactly one place.
*/

import { Button } from '@glad-labs/brand';
import { LemonSqueezyOverlay } from '@/components/LemonSqueezyOverlay';
import {
  CHECKOUT_LIVE,
  LS_PRO_URL,
  PRO_TRIAL_DAYS,
  FOUNDING_CTA_URL,
  FOUNDING_CTA_LABEL,
} from '@/lib/site.config';

export function ProCTA({ variant = 'primary' }) {
  if (CHECKOUT_LIVE) {
    return (
      <LemonSqueezyOverlay productUrl={LS_PRO_URL} variant={variant}>
        ▶ Start {PRO_TRIAL_DAYS}-day free trial
      </LemonSqueezyOverlay>
    );
  }

  return (
    <Button
      as="a"
      href={FOUNDING_CTA_URL}
      target="_blank"
      rel="noopener noreferrer"
      variant={variant}
    >
      {FOUNDING_CTA_LABEL} →
    </Button>
  );
}
