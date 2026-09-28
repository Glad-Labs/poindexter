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

    await expect(close).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(submit).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
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
