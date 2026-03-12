import React from 'react';
import { render, screen } from '@testing-library/react';
import PhaseNode from '../PhaseNode';

// Mock reactflow — Handle component just renders nothing in tests
vi.mock('reactflow', () => ({
  Handle: ({ type, position }) => (
    <div data-testid={`handle-${type}`} data-position={position} />
  ),
  Position: {
    Left: 'left',
    Right: 'right',
  },
}));

const makeData = (overrides = {}) => ({
  phase: {
    name: 'Research Phase',
    agent: 'research_agent',
    ...overrides,
  },
  label: 'Fallback Label',
});

describe('PhaseNode', () => {
  it('renders the phase name', () => {
    render(<PhaseNode data={makeData()} />);
    expect(screen.getByText('Research Phase')).toBeInTheDocument();
  });

  it('falls back to data.label when phase.name is missing', () => {
    const data = { phase: {}, label: 'Fallback Label' };
    render(<PhaseNode data={data} />);
    expect(screen.getByText('Fallback Label')).toBeInTheDocument();
  });

  it('renders source and target handles', () => {
    render(<PhaseNode data={makeData()} />);
    expect(screen.getByTestId('handle-target')).toBeInTheDocument();
    expect(screen.getByTestId('handle-source')).toBeInTheDocument();
  });

  it('renders agent chip when phase.agent is provided', () => {
    render(<PhaseNode data={makeData({ agent: 'creative_agent' })} />);
    expect(screen.getByText('creative_agent')).toBeInTheDocument();
  });

  it('does not render agent chip when phase.agent is absent', () => {
    const data = { phase: { name: 'Unnamed Phase' }, label: '' };
    render(<PhaseNode data={data} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders model chip when selected_model is provided', () => {
    render(
      <PhaseNode
        data={makeData({
          metadata: { selected_model: 'gpt-4o' },
        })}
      />
    );
    expect(screen.getByText('Model: gpt-4o')).toBeInTheDocument();
  });

  it('does not render model chip when no selected_model', () => {
    render(<PhaseNode data={makeData()} />);
    expect(screen.queryByText(/Model:/)).not.toBeInTheDocument();
  });

  it('renders required inputs count when input_schema has required fields', () => {
    render(
      <PhaseNode
        data={makeData({
          metadata: {
            input_schema: [
              { key: 'topic', required: true },
              { key: 'tone', required: false },
              { key: 'length', required: true },
            ],
          },
        })}
      />
    );
    expect(screen.getByText('Required inputs: 2')).toBeInTheDocument();
  });

  it('does not render required inputs count when none are required', () => {
    render(
      <PhaseNode
        data={makeData({
          metadata: {
            input_schema: [{ key: 'tone', required: false }],
          },
        })}
      />
    );
    expect(screen.queryByText(/Required inputs/)).not.toBeInTheDocument();
  });

  it('renders timeout when provided', () => {
    render(<PhaseNode data={makeData({ timeout_seconds: 120 })} />);
    expect(screen.getByText('120s')).toBeInTheDocument();
  });

  it('does not render timeout when absent', () => {
    render(<PhaseNode data={makeData()} />);
    expect(screen.queryByText(/s$/)).not.toBeInTheDocument();
  });

  it('renders max_retries when > 0', () => {
    render(<PhaseNode data={makeData({ max_retries: 3 })} />);
    expect(screen.getByText('3x')).toBeInTheDocument();
  });

  it('does not render retries when max_retries is 0', () => {
    render(<PhaseNode data={makeData({ max_retries: 0 })} />);
    expect(screen.queryByText(/x$/)).not.toBeInTheDocument();
  });

  it('applies selected styling when isSelected is true', () => {
    const { container } = render(
      <PhaseNode data={makeData()} isSelected={true} />
    );
    const paper = container.querySelector('.MuiPaper-root');
    expect(paper).toBeInTheDocument();
  });

  it('handles missing phase gracefully', () => {
    const data = { label: 'No Phase' };
    render(<PhaseNode data={data} />);
    expect(screen.getByText('No Phase')).toBeInTheDocument();
  });
});
