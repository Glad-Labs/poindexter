'use client';

/*
  <LemonSqueezyOverlay>
  Injects lemon.js once + exposes a button that opens a hosted checkout
  overlay (https://assets.lemonsqueezy.com/lemon.js → window.createLemonSqueezy).
  No redirect — overlay iframe sits on top of the storefront, user pays,
  overlay closes, you stay on gladlabs.ai.

  Usage:
    <LemonSqueezyOverlay
      productUrl={LS_PRO_URL}
      variant="primary"
    >
      ▶ Start 7-day free trial
    </LemonSqueezyOverlay>
*/

import { useEffect, useCallback } from 'react';
import Script from 'next/script';
import { Button } from '@glad-labs/brand';

let scriptMounted = false;
let lemonReady = false;

export function LemonSqueezyOverlay({
  productUrl,
  variant = 'primary',
  children,
  ...rest
}) {
  // Ensure the Lemon Squeezy script boots once per session. Subsequent
  // renders are no-ops. `window.createLemonSqueezy()` installs the global
  // handlers that .Url.Open() relies on.
  useEffect(() => {
    if (
      typeof window !== 'undefined' &&
      window.createLemonSqueezy &&
      !lemonReady
    ) {
      window.createLemonSqueezy();
      lemonReady = true;
    }
  }, []);

  const handleClick = useCallback(
    (e) => {
      e.preventDefault();
      if (typeof window === 'undefined') return;
      // If lemon.js hasn't finished loading yet, fall back to opening the
      // hosted checkout in a new tab — user never gets a dead button.
      if (window.LemonSqueezy && window.LemonSqueezy.Url) {
        window.LemonSqueezy.Url.Open(productUrl);
      } else {
        window.open(productUrl, '_blank', 'noopener');
      }
    },
    [productUrl]
  );

  return (
    <>
      {!scriptMounted && (
        <Script
          src="https://assets.lemonsqueezy.com/lemon.js"
          strategy="afterInteractive"
          onLoad={() => {
            if (typeof window !== 'undefined' && window.createLemonSqueezy) {
              window.createLemonSqueezy();
              lemonReady = true;
            }
          }}
        />
      )}
      <Button
        variant={variant}
        onClick={handleClick}
        data-lemon-url={productUrl}
        {...rest}
      >
        {children}
      </Button>
    </>
  );
}

// The script is injected unconditionally on first mount; guard against
// duplicate script tags across navigations by setting the flag at module
// scope. `next/script` also dedupes by src, so this is belt + suspenders.
scriptMounted = true;
