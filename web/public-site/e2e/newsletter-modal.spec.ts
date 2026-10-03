import { test, expect, type Locator, type Page } from '@playwright/test';

/**
 * Newsletter signup modal, in a real browser.
 *
 * components/__tests__/NewsletterModal.test.js covers the modal's logic in
 * jsdom, which does no layout and no hit-testing. These check what only a
 * browser shows: where a click lands, what paints over the dialog, where real
 * Tab presses go, and what stays reachable behind it. The click, the paint
 * order and the reachable footer were all broken while every unit test
 * passed.
 */

// A light page that carries the footer, where the modal's trigger lives.
const PAGE = '/legal/privacy';
const BANNER = '[aria-label="Cookie consent"]';

async function openModal(page: Page): Promise<Locator> {
  await page.goto(PAGE);
  // Before the modal opens this is the only "Get updates" button; the
  // dialog's submit button has the same label.
  await page.getByRole('button', { name: 'Get updates →' }).click();
  const dialog = page.getByRole('dialog', { name: 'Stay in the loop.' });
  await expect(dialog).toBeVisible();
  return dialog;
}

// Keeps the cookie banner out of the tests that are not about it: the banner
// reads this key on mount and stays hidden once a choice is stored.
async function withCookieChoiceMade(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'cookieConsent',
      JSON.stringify({ essential: true, analytics: false, advertising: false })
    );
  });
}

test.describe('Newsletter modal', () => {
  test('asks only for email and name', async ({ page }) => {
    await withCookieChoiceMade(page);
    const dialog = await openModal(page);

    const fields = await dialog
      .locator('input, select, textarea')
      .evaluateAll((els) => els.map((el) => el.id));
    expect(fields).toEqual([
      'newsletter-email',
      'newsletter-first-name',
      'newsletter-last-name',
    ]);
    await expect(dialog.getByRole('checkbox')).toHaveCount(0);
  });

  test('closes on a click outside the dialog, and only there', async ({
    page,
  }) => {
    await withCookieChoiceMade(page);
    const dialog = await openModal(page);

    await dialog.getByRole('heading', { name: 'Stay in the loop.' }).click();
    await expect(dialog).toBeVisible();

    // The dialog is centred, so the viewport's left edge is backdrop.
    const viewport = page.viewportSize();
    if (!viewport) throw new Error('no viewport size');
    await page.mouse.click(4, viewport.height / 2);
    await expect(dialog).toBeHidden();
    await expect(
      page.getByRole('button', { name: 'Get updates →' })
    ).toBeFocused();
  });

  test('keeps Tab and Shift+Tab inside the dialog', async ({ page }) => {
    await withCookieChoiceMade(page);
    const dialog = await openModal(page);
    const close = dialog.getByRole('button', { name: 'Close modal' });
    const submit = dialog.getByRole('button', { name: 'Get updates →' });
    // The small print's link to the privacy policy is the last control, so it
    // is where the trap wraps.
    const policy = dialog.getByRole('link', { name: /privacy policy/i });

    await expect(close).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(policy).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();

    // A real Tab from the submit button moves on to the link. The trap leaves
    // that step to the browser, and jsdom cannot show it.
    await submit.focus();
    await page.keyboard.press('Tab');
    await expect(policy).toBeFocused();
  });

  test('links to the newsletter section of the privacy policy, in a new tab', async ({
    page,
    context,
  }) => {
    await withCookieChoiceMade(page);
    const dialog = await openModal(page);
    const policy = dialog.getByRole('link', { name: /privacy policy/i });

    const tabOpened = context.waitForEvent('page');
    await policy.click();
    const tab = await tabOpened;
    await tab.waitForLoadState('domcontentloaded');

    // Reading the policy does not cost the visitor the form.
    await expect(dialog).toBeVisible();

    const url = new URL(tab.url());
    expect(url.pathname).toBe('/legal/privacy');
    expect(url.hash).toBe('#newsletter');

    // The fragment names a real section...
    const heading = tab.locator('#newsletter');
    await expect(heading).toBeVisible();

    // ...and the fixed header does not cover it once the page has come to
    // rest. Smooth scrolling (globals.css) animates the jump, and on the way
    // down the heading passes below the header, so a reading taken in flight
    // would say "clear" even if the page came to rest with the heading under
    // the header. Read it only once the heading is in view and has stopped
    // moving. (Waiting for scrollY to stop is not enough: it also holds still
    // for a moment before the animation starts.)
    const viewportHeight = await tab.evaluate(() => window.innerHeight);
    const rest: { top: number | null } = { top: null };
    await expect
      .poll(
        async () => {
          const before = await heading.boundingBox();
          await tab.waitForTimeout(250);
          const after = await heading.boundingBox();
          const stopped =
            before && after && before.y === after.y && after.y < viewportHeight;
          rest.top = stopped ? after.y : null;
          return rest.top;
        },
        {
          message:
            'the heading never stopped moving, or never scrolled into view',
        }
      )
      .not.toBeNull();

    const header = await tab.getByRole('banner').first().boundingBox();
    const restingTop = rest.top;
    if (!header || restingTop === null) throw new Error('not laid out');
    const headerBottom = header.y + header.height;
    expect(
      restingTop,
      `the heading rests at ${restingTop}px, under the fixed header that ends at ${headerBottom}px`
    ).toBeGreaterThanOrEqual(headerBottom);
  });

  test('leaves nothing outside the dialog reachable, the footer included', async ({
    page,
  }) => {
    await withCookieChoiceMade(page);
    await openModal(page);

    // The modal is rendered from the footer. Before it was portaled to
    // <body>, the footer held the dialog and was the one part of the page
    // left un-inerted: 18 links and buttons behind an open modal.
    const reachable = await page.evaluate(() => {
      const dialog = document.querySelector('[role="dialog"]');
      return Array.from(
        document.querySelectorAll<HTMLElement>(
          'a[href], button, input, select, textarea, [tabindex]'
        )
      )
        .filter((el) => !dialog?.contains(el) && !el.closest('[inert]'))
        .map((el) => el.outerHTML.slice(0, 80));
    });
    expect(reachable).toEqual([]);
  });

  test('paints above the cookie banner on a small phone', async ({ page }) => {
    // No stored cookie choice: the banner is up, as on a first visit.
    const dialog = await openModal(page);
    await page.setViewportSize({ width: 375, height: 667 });

    const submit = dialog.getByRole('button', { name: 'Get updates →' });
    const bannerBox = await page.locator(BANNER).boundingBox();
    const submitBox = await submit.boundingBox();
    if (!bannerBox || !submitBox)
      throw new Error('banner or submit not laid out');
    // The check below only means something while the banner reaches up
    // over the submit button, as it does at this size.
    expect(bannerBox.y).toBeLessThan(submitBox.y + submitBox.height);

    // What paints at the button's centre. The banner is inert while the
    // modal is open and hit-testing skips inert elements, so the probe lifts
    // that for one elementFromPoint call; otherwise it would see straight
    // through a banner painted over the button.
    const onTop = await page.evaluate(
      ({ x, y, banner }) => {
        const inertHost = document.querySelector(banner)?.closest('[inert]');
        inertHost?.removeAttribute('inert');
        try {
          const el = document.elementFromPoint(x, y);
          if (el?.closest('[role="dialog"]')) return 'dialog';
          if (el?.closest(banner)) return 'cookie banner';
          return el ? el.tagName : 'nothing';
        } finally {
          inertHost?.setAttribute('inert', '');
        }
      },
      {
        x: submitBox.x + submitBox.width / 2,
        y: submitBox.y + submitBox.height / 2,
        banner: BANNER,
      }
    );
    expect(onTop).toBe('dialog');
  });
});
