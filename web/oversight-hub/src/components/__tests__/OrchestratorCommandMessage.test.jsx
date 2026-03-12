import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import OrchestratorCommandMessage from '../OrchestratorCommandMessage';

// Mock useStore so tests don't need Zustand state
const mockStartExecution = vi.fn();
vi.mock('../../store/useStore', () => ({
  default: vi.fn((selector) =>
    selector({ startExecution: mockStartExecution })
  ),
}));

const baseMessage = {
  id: 'cmd-1',
  intent: 'generate',
  description: 'Generate a blog post',
  parameters: { topic: 'AI Trends', length: '1500' },
  modelHint: 'gpt-4',
};

describe('OrchestratorCommandMessage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the message description', () => {
    render(<OrchestratorCommandMessage message={baseMessage} />);
    expect(screen.getByText('Generate a blog post')).toBeInTheDocument();
  });

  it('renders Execute button', () => {
    render(<OrchestratorCommandMessage message={baseMessage} />);
    expect(screen.getByText('Execute')).toBeInTheDocument();
  });

  it('renders Edit and Cancel buttons initially', () => {
    render(<OrchestratorCommandMessage message={baseMessage} />);
    expect(screen.getByText('Edit')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('calls onExecute callback when Execute is clicked', () => {
    const onExecute = vi.fn();
    render(
      <OrchestratorCommandMessage message={baseMessage} onExecute={onExecute} />
    );
    fireEvent.click(screen.getByText('Execute'));
    expect(onExecute).toHaveBeenCalledTimes(1);
    expect(onExecute).toHaveBeenCalledWith({
      command: 'generate',
      parameters: baseMessage.parameters,
      mode: 'agent',
    });
  });

  it('calls startExecution from store when Execute is clicked', () => {
    render(<OrchestratorCommandMessage message={baseMessage} />);
    fireEvent.click(screen.getByText('Execute'));
    expect(mockStartExecution).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel callback when Cancel is clicked', () => {
    const onCancel = vi.fn();
    render(
      <OrchestratorCommandMessage message={baseMessage} onCancel={onCancel} />
    );
    fireEvent.click(screen.getByText('Cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('switches to edit mode when Edit is clicked', () => {
    render(<OrchestratorCommandMessage message={baseMessage} />);
    fireEvent.click(screen.getByText('Edit'));
    // In edit mode, "Cancel Edit" button appears
    expect(screen.getByText('Cancel Edit')).toBeInTheDocument();
    expect(screen.getByText('Confirm')).toBeInTheDocument();
  });

  it('shows Confirm and Cancel Edit buttons in edit mode', () => {
    render(<OrchestratorCommandMessage message={baseMessage} />);
    fireEvent.click(screen.getByText('Edit'));
    // Edit mode changes buttons
    expect(screen.getByText('Confirm')).toBeInTheDocument();
    expect(screen.getByText('Cancel Edit')).toBeInTheDocument();
    // Original Edit button should be gone
    expect(screen.queryByText(/^Edit$/)).not.toBeInTheDocument();
  });

  it('exits edit mode when Cancel Edit is clicked', () => {
    render(<OrchestratorCommandMessage message={baseMessage} />);
    fireEvent.click(screen.getByText('Edit'));
    fireEvent.click(screen.getByText('Cancel Edit'));
    // Back to normal mode — "Edit" button should be visible again
    expect(screen.getByText('Edit')).toBeInTheDocument();
    expect(screen.queryByText('Cancel Edit')).not.toBeInTheDocument();
  });

  it('renders correctly for different command types', () => {
    const analyzeMessage = {
      ...baseMessage,
      intent: 'analyze',
      description: 'Analyze data',
    };
    render(<OrchestratorCommandMessage message={analyzeMessage} />);
    expect(screen.getByText('Analyze data')).toBeInTheDocument();
  });

  it('renders with unknown intent falling back to generate type', () => {
    const unknownMessage = { ...baseMessage, intent: 'unknown_intent' };
    render(<OrchestratorCommandMessage message={unknownMessage} />);
    // Should still render without crashing
    expect(screen.getByText('Generate a blog post')).toBeInTheDocument();
  });
});
