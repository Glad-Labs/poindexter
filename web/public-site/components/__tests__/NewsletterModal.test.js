/**
 * Tests for components/NewsletterModal.tsx
 *
 * Covers:
 * - Renders nothing when isOpen=false
 * - Renders modal when isOpen=true
 * - Close button calls onClose
 * - Overlay click calls onClose
 * - Email required validation
 * - Category toggle
 * - Successful submission
 * - Error on API failure
 * - Loading state during submission
 */

import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react';
import NewsletterModal from '../NewsletterModal';

// Mock api-fastapi
jest.mock('../../lib/api-fastapi', () => ({
  subscribeToNewsletter: jest.fn(),
}));

import { subscribeToNewsletter } from '../../lib/api-fastapi';

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
});

afterEach(() => {
  jest.runOnlyPendingTimers();
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
    expect(screen.getByText('Stay Updated')).toBeInTheDocument();
  });

  test('renders email input field', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument();
  });

  test('renders Get Updates submit button', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(
      screen.getByRole('button', { name: /Get Updates/i })
    ).toBeInTheDocument();
  });

  test('renders all interest category checkboxes', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    const categories = [
      'AI',
      'Technology',
      'Automation',
      'Business',
      'Hardware',
      'Gaming',
    ];
    categories.forEach((cat) => {
      expect(screen.getByText(cat)).toBeInTheDocument();
    });
  });

  test('renders marketing consent checkbox', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    expect(
      screen.getByText(/I agree to receive marketing emails/i)
    ).toBeInTheDocument();
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
    // The overlay is aria-hidden div before the modal container
    const overlay = document.querySelector('[aria-hidden="true"]');
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
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

  test('does not call API when email is empty', async () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    await waitFor(() => {
      expect(subscribeToNewsletter).not.toHaveBeenCalled();
    });
  });
});

// ---------------------------------------------------------------------------
// Category toggle
// ---------------------------------------------------------------------------

describe('NewsletterModal category toggle', () => {
  test('clicking category checkbox toggles it', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    const aiLabel = screen.getByText('AI').closest('label');
    const checkbox = aiLabel.querySelector('input[type="checkbox"]');
    expect(checkbox.checked).toBe(false);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
  });

  test('clicking same category twice untoggles it', () => {
    render(<NewsletterModal {...DEFAULT_PROPS} />);
    const aiLabel = screen.getByText('AI').closest('label');
    const checkbox = aiLabel.querySelector('input[type="checkbox"]');
    fireEvent.click(checkbox);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Submission — success
// ---------------------------------------------------------------------------

describe('NewsletterModal submission success', () => {
  test('shows success message after successful API call', async () => {
    subscribeToNewsletter.mockResolvedValue({ success: true });
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'test@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Successfully subscribed/i)).toBeInTheDocument();
    });
  });

  test('calls subscribeToNewsletter with form data', async () => {
    subscribeToNewsletter.mockResolvedValue({ success: true });
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'user@test.com', name: 'email', type: 'email' },
    });
    fireEvent.change(screen.getByPlaceholderText('John'), {
      target: { value: 'Alice', name: 'firstName', type: 'text' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });

    await waitFor(() => {
      expect(subscribeToNewsletter).toHaveBeenCalledWith(
        expect.objectContaining({
          email: 'user@test.com',
          first_name: 'Alice',
        })
      );
    });
  });

  test('calls onClose after success timeout', async () => {
    const onClose = jest.fn();
    subscribeToNewsletter.mockResolvedValue({ success: true });
    render(<NewsletterModal isOpen={true} onClose={onClose} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'a@b.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
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
    subscribeToNewsletter.mockRejectedValue(new Error('Network error'));
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'fail@example.com', name: 'email', type: 'email' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Network error/i)).toBeInTheDocument();
    });
  });

  test('shows error when API returns success=false', async () => {
    subscribeToNewsletter.mockResolvedValue({
      success: false,
      message: 'Email already subscribed',
    });
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'dup@example.com', name: 'email', type: 'email' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
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
  test('button text changes to Subscribing... during submission', async () => {
    let resolveSubscribe;
    subscribeToNewsletter.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSubscribe = resolve;
        })
    );
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'slow@example.com', name: 'email', type: 'email' },
    });

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });

    await waitFor(() => {
      expect(screen.getByText('Subscribing...')).toBeInTheDocument();
    });

    // Resolve the promise to clean up
    await act(async () => {
      resolveSubscribe({ success: true });
    });
  });

  test('submit button is disabled during loading', async () => {
    let resolveSubscribe;
    subscribeToNewsletter.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSubscribe = resolve;
        })
    );
    render(<NewsletterModal {...DEFAULT_PROPS} />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'slow2@example.com', name: 'email', type: 'email' },
    });

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });

    await waitFor(() => {
      const btn = screen.getByText('Subscribing...');
      expect(btn).toBeDisabled();
    });

    await act(async () => {
      resolveSubscribe({ success: true });
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
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  it('success message container has role="status"', async () => {
    const { subscribeToNewsletter: sub } = require('../../lib/api-fastapi');
    sub.mockResolvedValue({ success: true });
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'ok@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });
    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
  });

  it('success message container has aria-live="polite"', async () => {
    const { subscribeToNewsletter: sub } = require('../../lib/api-fastapi');
    sub.mockResolvedValue({ success: true });
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'ok2@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
    });
  });

  it('error message container has role="alert"', async () => {
    const { subscribeToNewsletter: sub } = require('../../lib/api-fastapi');
    sub.mockRejectedValue(new Error('fail'));
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'fail@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
    });
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('error message container has aria-live="assertive"', async () => {
    const { subscribeToNewsletter: sub } = require('../../lib/api-fastapi');
    sub.mockRejectedValue(new Error('fail'));
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'fail2@example.com', name: 'email', type: 'email' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Get Updates/i }));
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

  it('Company label has htmlFor="newsletter-company"', () => {
    render(<NewsletterModal isOpen={true} onClose={jest.fn()} />);
    const label = screen.getByText('Company').closest('label');
    expect(label).toHaveAttribute('for', 'newsletter-company');
  });
});
