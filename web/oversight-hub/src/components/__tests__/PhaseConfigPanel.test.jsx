import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PhaseConfigPanel from '../PhaseConfigPanel';

const basePhase = {
  name: 'research_phase',
  agent: 'research_agent',
  description: 'Research step',
  timeout_seconds: 300,
  max_retries: 3,
  skip_on_error: false,
  required: true,
  metadata: {
    phase_type: 'research',
    selected_model: '',
    input_schema: [],
    phase_inputs: {},
  },
};

const defaultProps = {
  nodeId: 'node-1',
  phase: basePhase,
  availableModels: [],
  onUpdate: vi.fn(),
  onRemove: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PhaseConfigPanel', () => {
  it('renders the phase name in the heading', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    expect(screen.getByText('Phase: research_phase')).toBeInTheDocument();
  });

  it('renders Agent text field with current agent value', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    const agentInput = screen.getByDisplayValue('research_agent');
    expect(agentInput).toBeInTheDocument();
  });

  it('renders description textarea with current value', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    expect(screen.getByDisplayValue('Research step')).toBeInTheDocument();
  });

  it('renders Save Changes button (disabled when no changes)', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    expect(saveBtn).toBeDisabled();
  });

  it('enables Save Changes button after editing agent field', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    const agentInput = screen.getByDisplayValue('research_agent');
    fireEvent.change(agentInput, { target: { value: 'qa_agent' } });
    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    expect(saveBtn).not.toBeDisabled();
  });

  it('shows unsaved changes alert after editing', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    const agentInput = screen.getByDisplayValue('research_agent');
    fireEvent.change(agentInput, { target: { value: 'qa_agent' } });
    expect(screen.getByText(/You have unsaved changes/)).toBeInTheDocument();
  });

  it('calls onUpdate with nodeId and updated config when Save is clicked', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    const agentInput = screen.getByDisplayValue('research_agent');
    fireEvent.change(agentInput, { target: { value: 'qa_agent' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Changes/i }));
    expect(defaultProps.onUpdate).toHaveBeenCalledWith(
      'node-1',
      expect.objectContaining({ agent: 'qa_agent' })
    );
  });

  it('calls onRemove after user confirms the confirm dialog', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<PhaseConfigPanel {...defaultProps} />);
    fireEvent.click(screen.getByRole('button', { name: /Remove/i }));
    expect(defaultProps.onRemove).toHaveBeenCalledWith('node-1');
    vi.restoreAllMocks();
  });

  it('does NOT call onRemove when user cancels the confirm dialog', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<PhaseConfigPanel {...defaultProps} />);
    fireEvent.click(screen.getByRole('button', { name: /Remove/i }));
    expect(defaultProps.onRemove).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it('hides unsaved changes alert after saving', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    const agentInput = screen.getByDisplayValue('research_agent');
    fireEvent.change(agentInput, { target: { value: 'qa_agent' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Changes/i }));
    expect(
      screen.queryByText(/You have unsaved changes/)
    ).not.toBeInTheDocument();
  });

  it('resets config when phase prop changes', () => {
    const { rerender } = render(<PhaseConfigPanel {...defaultProps} />);
    rerender(
      <PhaseConfigPanel
        {...defaultProps}
        phase={{ ...basePhase, name: 'draft_phase', agent: 'creative_agent' }}
      />
    );
    expect(screen.getByText('Phase: draft_phase')).toBeInTheDocument();
    expect(screen.getByDisplayValue('creative_agent')).toBeInTheDocument();
  });

  it('renders model select dropdown', () => {
    const { container } = render(
      <PhaseConfigPanel
        {...defaultProps}
        availableModels={[
          { name: 'gpt-4o', displayName: 'GPT-4o', provider: 'openai' },
        ]}
      />
    );
    // MUI Select renders with role="combobox" — verify at least one exists
    const selects = container.querySelectorAll('[role="combobox"]');
    expect(selects.length).toBeGreaterThan(0);
  });

  it('renders text input fields from input_schema', () => {
    const phaseWithSchema = {
      ...basePhase,
      metadata: {
        ...basePhase.metadata,
        input_schema: [
          { key: 'topic', label: 'Topic', input_type: 'text', required: true },
        ],
      },
    };
    render(<PhaseConfigPanel {...defaultProps} phase={phaseWithSchema} />);
    // MUI TextField renders a label element — check by text content
    expect(screen.getByText('Topic')).toBeInTheDocument();
    // And the Phase Inputs section heading
    expect(screen.getByText('Phase Inputs')).toBeInTheDocument();
  });

  it('renders number input for number-type schema field', () => {
    const phaseWithNumber = {
      ...basePhase,
      metadata: {
        ...basePhase.metadata,
        input_schema: [
          { key: 'word_count', label: 'Word Count', input_type: 'number' },
        ],
      },
    };
    render(<PhaseConfigPanel {...defaultProps} phase={phaseWithNumber} />);
    // Number type TextField renders an <input type="number">
    const input = document.querySelector('input[type="number"]');
    expect(input).toBeInTheDocument();
  });

  it('renders boolean toggle for boolean-type schema field', () => {
    const phaseWithBoolean = {
      ...basePhase,
      metadata: {
        ...basePhase.metadata,
        input_schema: [
          {
            key: 'include_images',
            label: 'Include Images',
            input_type: 'boolean',
          },
        ],
      },
    };
    render(<PhaseConfigPanel {...defaultProps} phase={phaseWithBoolean} />);
    expect(screen.getByText('Include Images')).toBeInTheDocument();
  });

  it('shows Quality Threshold slider for phases with "assess" in name', () => {
    render(
      <PhaseConfigPanel
        {...defaultProps}
        phase={{ ...basePhase, name: 'quality_assess' }}
      />
    );
    expect(screen.getByText(/Quality Threshold/)).toBeInTheDocument();
  });

  it('does not show Quality Threshold slider for non-assess phases', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    expect(screen.queryByText(/Quality Threshold/)).not.toBeInTheDocument();
  });

  it('renders skip_on_error toggle', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    expect(
      screen.getByText('Skip if previous phase fails')
    ).toBeInTheDocument();
  });

  it('renders required phase toggle', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    expect(
      screen.getByText('Phase is required (workflow fails if this fails)')
    ).toBeInTheDocument();
  });

  it('renders timeout slider', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    expect(screen.getByText(/Timeout: 300s/)).toBeInTheDocument();
  });

  it('renders max retries slider', () => {
    render(<PhaseConfigPanel {...defaultProps} />);
    expect(screen.getByText(/Max Retries: 3/)).toBeInTheDocument();
  });

  it('uses phase_type from metadata as agent value when agent is missing', () => {
    const phaseNoAgent = {
      ...basePhase,
      agent: undefined,
      metadata: {
        ...basePhase.metadata,
        phase_type: 'qa_agent',
      },
    };
    render(<PhaseConfigPanel {...defaultProps} phase={phaseNoAgent} />);
    expect(screen.getByDisplayValue('qa_agent')).toBeInTheDocument();
  });
});
