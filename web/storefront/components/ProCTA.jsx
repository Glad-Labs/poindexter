'use client';

/*
  <ProCTA> — the single Pro call-to-action for the storefront.

  Gated launch: while site.config `CHECKOUT_LIVE` is false, every Pro CTA points
  to the founding-members community (no charge) instead of opening the Lemon
  Squeezy checkout. When CHECKOUT_LIVE flips true, the same component renders the
  LemonSqueezyOverlay trial button — callers don't change. This keeps the
  live/gated decision in exactly one place.

  GitHub username capture (glad-labs-stack#3216): Pro is delivered as a GitHub
  collaborator invite, so checkout needs the buyer's username. It rides the buy
  URL as `checkout[custom][github_username]`, which Lemon Squeezy carries as
  checkout custom_data — the delivery sync (services/pro_delivery.py) reads it
  and invites without any operator action. The field gates the overlay: no
  username, no checkout — a purchase we can't deliver is worse than one more
  keystroke before payment.
*/

import { useState } from 'react';
import { Button } from '@glad-labs/brand';
import { LemonSqueezyOverlay } from '@/components/LemonSqueezyOverlay';
import {
  CHECKOUT_LIVE,
  LS_PRO_URL,
  PRO_TRIAL_DAYS,
  FOUNDING_CTA_URL,
  FOUNDING_CTA_LABEL,
} from '@/lib/site.config';

// Mirror of services/pro_delivery.py::normalize_github_username — accepts
// "@name", profile URLs, stray whitespace; returns '' when nothing valid
// survives so the caller treats it as missing.
export function normalizeGithubUsername(raw) {
  if (!raw) return '';
  let name = String(raw).trim().replace(/\/+$/, '');
  if (name.includes('/')) name = name.split('/').pop();
  name = name.replace(/^@/, '').trim();
  return /^[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}$/.test(name) ? name : '';
}

export function ProCTA({ variant = 'primary' }) {
  const [githubUsername, setGithubUsername] = useState('');
  const [showHint, setShowHint] = useState(false);

  if (!CHECKOUT_LIVE) {
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

  const cleaned = normalizeGithubUsername(githubUsername);
  const checkoutUrl = cleaned
    ? `${LS_PRO_URL}?checkout[custom][github_username]=${encodeURIComponent(cleaned)}`
    : LS_PRO_URL;
  const ctaLabel = `▶ Start ${PRO_TRIAL_DAYS}-day free trial`;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        maxWidth: '20rem',
      }}
    >
      <input
        type="text"
        value={githubUsername}
        onChange={(e) => {
          setGithubUsername(e.target.value);
          if (showHint) setShowHint(false);
        }}
        placeholder="GitHub username (for repo access)"
        aria-label="GitHub username — Pro is delivered as a private-repo invite"
        autoComplete="off"
        spellCheck={false}
        style={{
          font: 'inherit',
          padding: '0.55rem 0.75rem',
          border: '1px solid currentColor',
          borderRadius: '4px',
          background: 'transparent',
          color: 'inherit',
        }}
      />
      {cleaned ? (
        <LemonSqueezyOverlay productUrl={checkoutUrl} variant={variant}>
          {ctaLabel}
        </LemonSqueezyOverlay>
      ) : (
        <Button variant={variant} onClick={() => setShowHint(true)}>
          {ctaLabel}
        </Button>
      )}
      {showHint && !cleaned && (
        <p role="alert" style={{ fontSize: '0.8rem', margin: 0 }}>
          Enter your GitHub username first — Pro is delivered as a private-repo
          invite to that account, minutes after checkout.
        </p>
      )}
    </div>
  );
}
