/**
 * AdSenseScript Component Tests
 *
 * Tests the AdSense script loader component.
 * Verifies: No render when ID missing, Script element when ID set
 */
import { render } from '@testing-library/react';
import AdSenseScript from '../AdSenseScript';

// Mock next/script to render a regular script tag
jest.mock('next/script', () => {
  return ({ src, onLoad, onError, ...props }) => (
    <script data-testid="adsense-script" src={src} {...props} />
  );
});

jest.mock('@sentry/nextjs', () => ({
  captureException: jest.fn(),
}));

const originalEnv = process.env;

beforeEach(() => {
  process.env = { ...originalEnv };
  delete process.env.NEXT_PUBLIC_ADSENSE_ID;
});

afterAll(() => {
  process.env = originalEnv;
});

describe('AdSenseScript Component', () => {
  it('should render with default ADSENSE_ID when env var is not set', async () => {
    // Component has a hardcoded fallback ID (ca-pub-4578747062758519)
    const { findByTestId } = render(<AdSenseScript />);

    const script = await findByTestId('adsense-script');
    expect(script).toBeInTheDocument();
    expect(script).toHaveAttribute(
      'src',
      expect.stringContaining('ca-pub-4578747062758519')
    );
  });

  it('should render Script element when ADSENSE_ID is set and component is mounted', async () => {
    process.env.NEXT_PUBLIC_ADSENSE_ID = 'ca-pub-9999999999';

    const { findByTestId } = render(<AdSenseScript />);

    // After useEffect sets mounted=true, it should render the Script
    const script = await findByTestId('adsense-script');
    expect(script).toBeInTheDocument();
    expect(script).toHaveAttribute(
      'src',
      expect.stringContaining('ca-pub-9999999999')
    );
  });
});
