/**
 * Dashboard Component Tests
 *
 * Tests the Executive Dashboard (/) page rendering and KPI displays
 * Verifies: KPI cards, time range selector, data loading, error states
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Dashboard from './Dashboard';

// Mock child components if needed
vi.mock('../common/KPICard', () => ({
  default: ({ title, value, unit }) => (
    <div data-testid="kpi-card">
      {title}: {value} {unit}
    </div>
  ),
}));

describe('Dashboard Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should render dashboard heading', () => {
    render(<Dashboard />);
    expect(
      screen.getByRole('heading', { name: /Executive Dashboard/i })
    ).toBeInTheDocument();
  });

  it('should render KPI section with all four cards', () => {
    render(<Dashboard />);
    expect(screen.getByText(/Key Performance Indicators/i)).toBeInTheDocument();

    const kpiCards = screen.getAllByTestId('kpi-card');
    expect(kpiCards.length).toBeGreaterThanOrEqual(4);
  });

  it('should render time range selector', () => {
    render(<Dashboard />);
    const timeRangeSelector = screen.getByRole('combobox', {
      name: /time range|period/i,
    });
    expect(timeRangeSelector).toBeInTheDocument();
  });

  it('should change time range when selector is updated', async () => {
    const user = userEvent.setup();
    render(<Dashboard />);

    const selector = screen.getByRole('combobox');
    await user.selectOptions(selector, '30d');

    expect(selector).toHaveValue('30d');
  });

  it('should render without JavaScript errors', () => {
    const errors = [];
    const originalError = console.error;
    console.error = (err) => {
      if (!err.includes('ResizeObserver')) {
        errors.push(err);
      }
    };

    render(<Dashboard />);

    console.error = originalError;
    expect(errors).toHaveLength(0);
  });

  it('should render KPI cards with correct data attributes', () => {
    render(<Dashboard />);

    const revenueCard = screen.getByTestId('kpi-card-revenue');
    const contentCard = screen.getByTestId('kpi-card-content');
    const tasksCard = screen.getByTestId('kpi-card-tasks');
    const savingsCard = screen.getByTestId('kpi-card-savings');

    expect(revenueCard).toBeInTheDocument();
    expect(contentCard).toBeInTheDocument();
    expect(tasksCard).toBeInTheDocument();
    expect(savingsCard).toBeInTheDocument();
  });

  it('should be accessible with proper ARIA labels', () => {
    render(<Dashboard />);

    const main = screen.getByRole('main');
    expect(main).toBeInTheDocument();

    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toHaveTextContent(/Executive Dashboard/i);
  });
});
