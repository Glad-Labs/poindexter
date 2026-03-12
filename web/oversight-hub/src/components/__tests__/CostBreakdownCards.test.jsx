import React from 'react';
import { render, screen } from '@testing-library/react';
import CostBreakdownCards from '../CostBreakdownCards';

describe('CostBreakdownCards', () => {
  it('renders empty-state message when both props are empty', () => {
    render(<CostBreakdownCards costByPhase={{}} costByModel={{}} />);
    expect(screen.getByText('No cost data available')).toBeInTheDocument();
  });

  it('renders empty-state when no props are provided (defaults)', () => {
    render(<CostBreakdownCards />);
    expect(screen.getByText('No cost data available')).toBeInTheDocument();
  });

  it('renders cost breakdown heading when data is present', () => {
    render(
      <CostBreakdownCards
        costByPhase={{ research: 0.001, draft: 0.002 }}
        costByModel={{ ollama: 0.0005 }}
      />
    );
    expect(screen.getByText(/Cost Breakdown Analysis/i)).toBeInTheDocument();
  });

  it('renders "By Pipeline Phase" section with phase data', () => {
    render(
      <CostBreakdownCards
        costByPhase={{ research: 0.001, draft: 0.002 }}
        costByModel={{}}
      />
    );
    expect(screen.getByText('By Pipeline Phase')).toBeInTheDocument();
    expect(screen.getByText('Research')).toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
  });

  it('renders "By AI Model" section with model data', () => {
    render(
      <CostBreakdownCards
        costByPhase={{}}
        costByModel={{ ollama: 0.0, gpt4: 0.001 }}
      />
    );
    // ollama has 0 cost so it's filtered out; only gpt4 should appear
    expect(screen.getByText('By AI Model')).toBeInTheDocument();
    expect(screen.getByText('Gpt4')).toBeInTheDocument();
  });

  it('filters out zero-cost items from the display', () => {
    render(
      <CostBreakdownCards
        costByPhase={{ research: 0, draft: 0.005 }}
        costByModel={{}}
      />
    );
    // "Research" should be excluded (cost = 0); "Draft" should appear
    expect(screen.queryByText('Research')).not.toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
  });

  it('renders summary stat cards: Total Phase Cost and Total Model Cost', () => {
    render(
      <CostBreakdownCards
        costByPhase={{ research: 0.001 }}
        costByModel={{ ollama: 0.002 }}
      />
    );
    expect(screen.getByText('Total Phase Cost')).toBeInTheDocument();
    expect(screen.getByText('Total Model Cost')).toBeInTheDocument();
    expect(screen.getByText('Combined Cost')).toBeInTheDocument();
  });

  it('calculates and displays correct percentage', () => {
    render(
      <CostBreakdownCards
        costByPhase={{ research: 1, draft: 1 }}
        costByModel={{}}
      />
    );
    // Both phases are equal so each should be 50%
    const percentages = screen.getAllByText('50.0%');
    expect(percentages.length).toBeGreaterThanOrEqual(2);
  });
});
