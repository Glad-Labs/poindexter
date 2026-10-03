/**
 * Tests for components/NewsletterModal.tsx
 *
 * Covers:
 * - Renders nothing when isOpen=false
 * - Renders modal when isOpen=true
 * - Asks for, and sends, only email + first/last name (no company,
 *   interests or marketing-consent tick: nothing downstream read them)
 * - The privacy line says what is kept and claims nothing more, and links to
 *   the privacy policy's newsletter section
 * - Close button calls onClose
 * - Overlay click calls onClose
 * - Email required validation
 * - Successful submission
 * - Error on API failure
 * - Loading state during submission
 * - a11y: dialog semantics, live regions, label associations, focus trap,
 *   inert background, focus restore, and focus after submitting
 */

import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  within,
} from '@testing-library/react';
import NewsletterModal from '../NewsletterModal';

// Mock Sentry to avoid import errors
jest.mock('@sentry/nextjs', () => ({
  captureException: jest.fn(),
}));

// Mock global fetch (the component uses fetch('/api/newsletter/subscribe', ...))
const mockFetch = jest.fn();
global.fetch = mockFetch;

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockFetch.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

const DEFAULT_PROPS = {
  isOpen: true,
  onClose: jest.fn(),
};

// ---------------------------------------------------------------------------
// Visibility
// ---------------------------------------------------------------------------

describe('NewsletterModal visibility', () => {
  test('renders nothing when isOpen is false', () => {
    const { container } = render(
      <NewsletterModal isOpen={false} onClose={jest.fn()} />
    );
    expect(container.firstChild).toBeNull();
  });

  test('renders modal content when isOpen is true', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(screen.getByText('Stay in the loop.')).toBeInTheDocument();
  });

  test('renders email input field', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument();
  });

  test('renders Get updates submit button', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(
      screen.getByRole('button', { name: /Get updates/i })
    ).toBeInTheDocument();
  });

  // Pins the whole field set: a new field fails this until someone adds the
  // code that stores and reads it (data minimisation).
  test('asks only for email and first/last name', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    const fields = Array.from(
      document.querySelectorAll('form input, form select, form textarea')
    ).map((el) => el.id);
    expect(fields).toEqual([
      'newsletter-email',
      'newsletter-first-name',
      'newsletter-last-name',
    ]);
  });

  test('does not ask for company, interests or marketing consent', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(screen.queryByLabelText(/company/i)).toBeNull();
    expect(screen.queryByText(/interests/i)).toBeNull();
    expect(screen.queryByText(/marketing emails/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Privacy line
// ---------------------------------------------------------------------------

describe('NewsletterModal privacy line', () => {
  test('says what the signup keeps', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(
      screen.getByText(
        'We use your email and name for these updates and nothing else.'
      )
    ).toBeInTheDocument();
  });

  test('does not claim to collect the IP address or user-agent', () => {
    // It used to, and nothing stored either one with a subscription.
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(screen.queryByText(/IP address/i)).toBeNull();
    expect(screen.queryByText(/user-agent/i)).toBeNull();
  });

  test('links to the newsletter section of the privacy policy', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    const link = within(screen.getByRole('dialog')).getByRole('link', {
      name: /privacy policy/i,
    });
    expect(link).toHaveAttribute('href', '/legal/privacy#newsletter');
  });

  test('opens the policy in a new tab, so the form is not lost', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    const link = screen.getByRole('link', { name: /privacy policy/i });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    // The new tab is announced, not just implied.
    expect(link).toHaveAccessibleName(/opens in a new tab/i);
  });
});

// ---------------------------------------------------------------------------
// Close behavior
// ---------------------------------------------------------------------------

describe('NewsletterModal close behavior', () => {
  test('close button calls onClose', () => {
    const onClose = jest.fn();
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    fireEvent.click(screen.getByLabelText('Close modal'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('overlay click calls onClose', () => {
    const onClose = jest.fn();
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    // The overlay sits just before the layer that centres the dialog. (A
    // browser only delivers a click to it because that layer is
    // pointer-events-none; e2e/newsletter-modal.spec.ts checks that part.)
    const overlay =
      screen.getByRole('dialog').parentElement.previousElementSibling;
    expect(overlay).toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('a click inside the dialog does not close it', () => {
    const onClose = jest.fn();
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    fireEvent.click(screen.getByText('Stay in the loop.'));
    expect(onClose).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

describe('NewsletterModal validation', () => {
  test('shows error when email is empty and form is submitted', async () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    // Use fireEvent.submit on the form directly to bypass native required constraint
    const form = document.querySelector('form');
    fireEvent.submit(form);
    await waitFor(() => {
      expect(screen.getByText('Email is required')).toBeInTheDocument();
    });
  });

  test('does not call fetch when email is empty', async () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    const form = document.querySelector('form');
    fireEvent.submit(form);
    await waitFor(() => {
      expect(mockFetch).not.toHaveBeenCalled();
    });
  });
});

// ---------------------------------------------------------------------------
// Submission — success
// ---------------------------------------------------------------------------

describe('NewsletterModal submission success', () => {
  test('shows success message after successful API call', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true }),
    });
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'test@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Successfully subscribed/i)).toBeInTheDocument();
    });
  });

  test('sends only email and first/last name', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true }),
    });
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'user@test.com', name: 'email', type: 'email' },
    });
    fireEvent.change(screen.getByPlaceholderText('Jane'), {
      target: { value: 'Alice', name: 'firstName', type: 'text' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/newsletter/subscribe',
        expect.objectContaining({
          method: 'POST',
          body: expect.any(String),
        })
      );
      // Exact match, not objectContaining: an extra key in the payload is
      // exactly the regression this guards against.
      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body).toEqual({
        email: 'user@test.com',
        first_name: 'Alice',
        last_name: '',
      });
    });
  });

  test('calls onClose after success timeout', async () => {
    const onClose = jest.fn();
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true }),
    });
    render(<NewsletterModal isOpen={true} onClose={onClose} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'a@b.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });

    // Advance the 2-second close timer
    await act(async () => {
      jest.advanceTimersByTime(2100);
    });

    expect(onClose).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Submission — error
// ---------------------------------------------------------------------------

describe('NewsletterModal submission error', () => {
  test('shows error message on API failure', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: 'Network error' }),
    });
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'fail@example.com', name: 'email', type: 'email' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Network error/i)).toBeInTheDocument();
    });
  });

  test('shows error when API returns success=false', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        success: false,
        message: 'Email already subscribed',
      }),
    });
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'dup@example.com', name: 'email', type: 'email' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Email already subscribed/i)).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe('NewsletterModal loading state', () => {
  test('button text changes to Subscribing… during submission', async () => {
    let resolveFetch;
    mockFetch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        })
    );
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'slow@example.com', name: 'email', type: 'email' },
    });

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });

    await waitFor(() => {
      expect(screen.getByText('Subscribing…')).toBeInTheDocument();
    });

    // Resolve the promise to clean up
    await act(async () => {
      resolveFetch({ ok: true, json: async () => ({ success: true }) });
    });
  });

  test('submit button is disabled during loading', async () => {
    let resolveFetch;
    mockFetch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        })
    );
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'slow2@example.com', name: 'email', type: 'email' },
    });

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });

    await waitFor(() => {
      const btn = screen.getByText('Subscribing…');
      expect(btn).toBeDisabled();
    });

    await act(async () => {
      resolveFetch({ ok: true, json: async () => ({ success: true }) });
    });
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #762: modal dialog semantics
// ---------------------------------------------------------------------------

describe('NewsletterModal — a11y: dialog role and Escape close (issue #762)', () => {
  const onClose = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  it('modal container has role="dialog"', () => {
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('modal container has aria-modal="true"', () => {
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('modal container has aria-labelledby="newsletter-dialog-title"', () => {
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute(
      'aria-labelledby',
      'newsletter-dialog-title'
    );
  });

  it('h2 heading has id="newsletter-dialog-title"', () => {
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    const heading = document.getElementById('newsletter-dialog-title');
    expect(heading).toBeInTheDocument();
    expect(heading.tagName).toBe('H2');
  });

  it('pressing Escape calls onClose', () => {
    render(<NewsletterModal isOpen={true} onClose={onClose} />);
    const dialog = screen.getByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #779: submission status announced via live region
// ---------------------------------------------------------------------------

describe('NewsletterModal — a11y: status message live region (issue #779)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
  });

  afterEach(() => {
    // The success path leaves the 2 s close timer pending; it sets state, so
    // flush it inside act() or React warns about an unwrapped update.
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('success message container has role="status"', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true }),
    });
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'ok@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });
    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
  });

  it('success message container has aria-live="polite"', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ success: true }),
    });
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'ok2@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
    });
  });

  it('error message container has role="alert"', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: 'fail' }),
    });
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'fail@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('error message container has aria-live="assertive"', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: 'fail' }),
    });
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'fail2@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get updates/i }));
    });
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveAttribute(
        'aria-live',
        'assertive'
      );
    });
  });
});

// ---------------------------------------------------------------------------
// a11y — issue #781: form fields have htmlFor/id associations
// ---------------------------------------------------------------------------

describe('NewsletterModal — a11y: form field label associations (issue #781)', () => {
  it('Email label has htmlFor="newsletter-email"', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    const emailLabel = screen.getByText(/Email \*/i).closest('label');
    expect(emailLabel).toHaveAttribute('for', 'newsletter-email');
  });

  it('Email input has id="newsletter-email"', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    const input = document.getElementById('newsletter-email');
    expect(input).toBeInTheDocument();
    expect(input.type).toBe('email');
  });

  it('First Name label has htmlFor="newsletter-first-name"', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    const label = screen.getByText('First Name').closest('label');
    expect(label).toHaveAttribute('for', 'newsletter-first-name');
  });

  it('Last Name label has htmlFor="newsletter-last-name"', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    const label = screen.getByText('Last Name').closest('label');
    expect(label).toHaveAttribute('for', 'newsletter-last-name');
  });
});

// ---------------------------------------------------------------------------
// a11y — issues #762 / #978a: focus trap, inert background, focus restore
// ---------------------------------------------------------------------------

describe('NewsletterModal — a11y: focus trap and background (issues #762, #978a)', () => {
  const closeButton = () => screen.getByLabelText('Close modal');
  const submitButton = () =>
    screen.getByRole('button', { name: /Get updates/i });
  // The last control in the dialog: the small print's link to the privacy
  // policy follows the submit button.
  const policyLink = () =>
    screen.getByRole('link', { name: /privacy policy/i });

  // Page content outside the modal; removed even when an assertion fails, so
  // a stray inert node can't leak into later tests.
  let added = [];
  const addToPage = (el) => {
    document.body.appendChild(el);
    added.push(el);
    return el;
  };
  afterEach(() => {
    added.forEach((el) => el.remove());
    added = [];
  });

  it('focuses the first control, Close, when it opens', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    expect(closeButton()).toHaveFocus();
  });

  it('Tab on the last control, the privacy policy link, wraps to the first', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    policyLink().focus();
    fireEvent.keyDown(policyLink(), { key: 'Tab' });
    expect(closeButton()).toHaveFocus();
  });

  it('Shift+Tab on the first control wraps to the last, the privacy policy link', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.keyDown(closeButton(), { key: 'Tab', shiftKey: true });
    expect(policyLink()).toHaveFocus();
  });

  it('Tab on the submit button is left to the browser: the link follows it', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    submitButton().focus();
    fireEvent.keyDown(submitButton(), { key: 'Tab' });
    // Not wrapped to Close. (jsdom does not move focus on Tab; the e2e spec
    // checks that a real Tab reaches the link.)
    expect(submitButton()).toHaveFocus();
  });

  it('the privacy policy link is the last focusable control in the dialog', () => {
    // The trap wraps on whichever control is last in the DOM, so a control
    // added after the link moves the wrap point. This fails then, which is the
    // cue to update the wrap tests above and the e2e spec.
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    const focusable = screen
      .getByRole('dialog')
      .querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
    expect(focusable[focusable.length - 1]).toBe(policyLink());
  });

  it('makes the page behind it inert while open, and restores it on close', () => {
    const page = addToPage(document.createElement('main'));
    const onClose = jest.fn();
    const { rerender } = render(
      <NewsletterModal isOpen={true} onClose={onClose} />
    );

    expect(page).toHaveAttribute('inert');
    expect(page).toHaveAttribute('aria-hidden', 'true');

    rerender(<NewsletterModal isOpen={false} onClose={onClose} />);
    expect(page).not.toHaveAttribute('inert');
    expect(page).not.toHaveAttribute('aria-hidden');
  });

  it('inerts the part of the page it is rendered from, e.g. the footer', () => {
    // The site renders this modal inside <footer>. Rendered in place, the
    // footer held the dialog, so it was the one part of the page left
    // reachable; the modal is portaled to <body> so it is inerted too.
    render(
      <footer>
        <a href="/about">About</a>
        <NewsletterModal isOpen={true} onClose={jest.fn()} />
      </footer>
    );
    const link = screen.getByText('About');
    expect(link.closest('[inert]')).not.toBeNull();
    expect(link.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(screen.getByRole('dialog').closest('footer')).toBeNull();
  });

  it('returns focus to the element that opened it', () => {
    const trigger = addToPage(document.createElement('button'));
    trigger.focus();
    const onClose = jest.fn();
    const { rerender } = render(
      <NewsletterModal isOpen={true} onClose={onClose} />
    );
    expect(trigger).not.toHaveFocus();

    rerender(<NewsletterModal isOpen={false} onClose={onClose} />);
    expect(trigger).toHaveFocus();
  });
});

// ---------------------------------------------------------------------------
// a11y — focus after submitting from the button
// ---------------------------------------------------------------------------

describe('NewsletterModal — a11y: focus after submitting', () => {
  // A browser moves focus to <body> when the focused submit button is
  // disabled for the request. jsdom leaves it on the button (and its blur()
  // ignores a disabled element), so these tests move it to <body> by focusing
  // a throwaway element and removing it. Without that step they would pass
  // with no fix at all.
  const dropFocusToBody = () => {
    const tmp = document.createElement('input');
    document.body.appendChild(tmp);
    tmp.focus();
    tmp.remove();
  };

  const startSubmitFromButton = () => {
    let resolveFetch;
    mockFetch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        })
    );
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'kb@example.com', name: 'email', type: 'email' },
    });
    // Same DOM node throughout; its label reads "Subscribing…" mid-request.
    const submit = screen.getByRole('button', { name: /Get updates/i });
    submit.focus();
    fireEvent.click(submit);
    expect(submit).toBeDisabled();
    dropFocusToBody();
    expect(document.body).toHaveFocus();
    const finish = () =>
      act(async () => {
        resolveFetch({ ok: false, json: async () => ({ detail: 'fail' }) });
      });
    return { submit, finish };
  };

  it('hands focus back to the submit button when the request settles', async () => {
    const { submit, finish } = startSubmitFromButton();
    await finish();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(submit).toHaveFocus();
  });

  it('does not take focus back from wherever the user moved it', async () => {
    const { finish } = startSubmitFromButton();
    const email = screen.getByPlaceholderText('you@example.com');
    email.focus();
    await finish();
    expect(email).toHaveFocus();
  });
});
