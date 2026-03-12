/**
 * Tests for components/common/CommandPane.jsx
 *
 * Covers:
 * - Renders title and key UI elements
 * - Agent selector has all expected agents
 * - Model selector has all expected models
 * - Conversation/Agentic mode toggle buttons
 * - Delegate Task button toggles delegateMode
 * - Context panel toggle
 * - Message input: calls addMessage on send
 * - Command parsing logic (financial/market/compliance detection)
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// --- Mock chatscope (very large library, not needed in unit tests) ---
vi.mock('@chatscope/chat-ui-kit-react', () => ({
  MainContainer: ({ children }) => (
    <div data-testid="main-container">{children}</div>
  ),
  ChatContainer: ({ children }) => <div>{children}</div>,
  MessageList: ({ children }) => (
    <div data-testid="message-list">{children}</div>
  ),
  MessageInput: ({ placeholder, onSend }) => (
    <div>
      <input
        data-testid="message-input"
        placeholder={placeholder}
        onChange={() => {}}
      />
      <button
        data-testid="send-button"
        onClick={() => onSend && onSend('test command')}
      >
        Send
      </button>
    </div>
  ),
  TypingIndicator: ({ content }) => <div>{content}</div>,
  Message: ({ model }) => (
    <div data-testid="chat-message">{model?.message}</div>
  ),
}));

vi.mock(
  '@chatscope/chat-ui-kit-styles/dist/default/styles.min.css',
  () => ({})
);

// Mock the CSS import
vi.mock('../CommandPane.css', () => ({}));

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// Mock sub-components
vi.mock('../../OrchestratorCommandMessage', () => ({
  default: () => <div data-testid="orchestrator-command-msg" />,
}));
vi.mock('../../OrchestratorStatusMessage', () => ({
  default: () => <div data-testid="orchestrator-status-msg" />,
}));
vi.mock('../../OrchestratorResultMessage', () => ({
  default: () => <div data-testid="orchestrator-result-msg" />,
}));
vi.mock('../../OrchestratorErrorMessage', () => ({
  default: () => <div data-testid="orchestrator-error-msg" />,
}));

// Mock messageTypes
vi.mock('../../../lib/messageTypes', () => ({
  MESSAGE_TYPES: {
    ORCHESTRATOR_COMMAND: 'orchestrator_command',
    ORCHESTRATOR_STATUS: 'orchestrator_status',
    ORCHESTRATOR_RESULT: 'orchestrator_result',
    ORCHESTRATOR_ERROR: 'orchestrator_error',
  },
}));

// Mock cofounderAgentClient (dynamic import)
vi.mock('../../../services/cofounderAgentClient', () => ({
  makeRequest: vi.fn().mockResolvedValue({ response: 'Test result' }),
  generateTaskImage: vi.fn(),
}));

// Mock Zustand store
const mockAddMessage = vi.fn();
const mockUpdateMessage = vi.fn();
const mockStartExecution = vi.fn();
const mockCompleteExecution = vi.fn();
const mockFailExecution = vi.fn();
const mockRemoveMessage = vi.fn();

vi.mock('../../../store/useStore', () => ({
  default: vi.fn(() => ({
    selectedTask: null,
    tasks: [],
    messages: [],
    addMessage: mockAddMessage,
    updateMessage: mockUpdateMessage,
    startExecution: mockStartExecution,
    completeExecution: mockCompleteExecution,
    failExecution: mockFailExecution,
    removeMessage: mockRemoveMessage,
  })),
}));

import CommandPane from '../CommandPane';

describe('CommandPane', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the Poindexter title', () => {
    render(<CommandPane />);
    expect(screen.getByText('Poindexter')).toBeInTheDocument();
  });

  it('renders Conversation and Agentic mode buttons', () => {
    render(<CommandPane />);
    expect(screen.getByTitle(/Conversation Mode/i)).toBeInTheDocument();
    expect(screen.getByTitle(/Agentic Mode/i)).toBeInTheDocument();
  });

  it('renders Agent selector', () => {
    render(<CommandPane />);
    const agentSelect = document.querySelector('#ai-agent');
    expect(agentSelect).toBeTruthy();
  });

  it('agent selector has all expected agents', () => {
    render(<CommandPane />);
    const agentSelect = document.querySelector('#ai-agent');
    const options = Array.from(agentSelect.options).map((o) => o.value);
    expect(options).toContain('content');
    expect(options).toContain('financial');
    expect(options).toContain('market');
    expect(options).toContain('compliance');
    expect(options).toContain('orchestrator');
  });

  it('renders Model selector', () => {
    render(<CommandPane />);
    const modelSelect = document.querySelector('#ai-model');
    expect(modelSelect).toBeTruthy();
  });

  it('model selector has all expected models', () => {
    render(<CommandPane />);
    const modelSelect = document.querySelector('#ai-model');
    const options = Array.from(modelSelect.options).map((o) => o.value);
    expect(options).toContain('gpt-4');
    expect(options).toContain('gpt-3.5');
    expect(options).toContain('claude-3');
    expect(options).toContain('local');
  });

  it('renders Delegate Task button', () => {
    render(<CommandPane />);
    expect(
      screen.getByTitle(/Delegate tasks to Poindexter/i)
    ).toBeInTheDocument();
  });

  it('renders message input with placeholder', () => {
    render(<CommandPane />);
    expect(
      screen.getByPlaceholderText(/Ask for help, delegate tasks/i)
    ).toBeInTheDocument();
  });

  it('clicking Agentic mode button activates agentic mode', () => {
    render(<CommandPane />);
    const agenticBtn = screen.getByTitle(/Agentic Mode/i);
    fireEvent.click(agenticBtn);
    expect(agenticBtn.className).toContain('active');
  });

  it('clicking Conversation mode button restores conversation mode', () => {
    render(<CommandPane />);
    const agenticBtn = screen.getByTitle(/Agentic Mode/i);
    const convoBtn = screen.getByTitle(/Conversation Mode/i);
    // Activate agentic first
    fireEvent.click(agenticBtn);
    // Restore conversation
    fireEvent.click(convoBtn);
    expect(convoBtn.className).toContain('active');
  });

  it('context toggle button shows/hides context panel', () => {
    render(<CommandPane />);
    const toggleBtn = screen.getByTitle(/Toggle context information/i);
    // Context panel not shown initially
    expect(screen.queryByText('Current Context')).not.toBeInTheDocument();
    // Click to show
    fireEvent.click(toggleBtn);
    expect(screen.getByText('Current Context')).toBeInTheDocument();
    // Click to hide
    fireEvent.click(toggleBtn);
    expect(screen.queryByText('Current Context')).not.toBeInTheDocument();
  });

  it('Delegate Task button calls addMessage with delegate message', () => {
    render(<CommandPane />);
    const delegateBtn = screen.getByTitle(/Delegate tasks to Poindexter/i);
    fireEvent.click(delegateBtn);
    expect(mockAddMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'text',
        direction: 'incoming',
      })
    );
  });

  it('send button triggers addMessage calls for user and command messages', async () => {
    render(<CommandPane />);
    const sendButton = screen.getByTestId('send-button');
    fireEvent.click(sendButton);
    await waitFor(() => {
      // addMessage should be called: once for user message, once for command message
      expect(mockAddMessage).toHaveBeenCalledTimes(2);
    });
  });

  it('changing agent selector updates selected agent', () => {
    render(<CommandPane />);
    const agentSelect = document.querySelector('#ai-agent');
    fireEvent.change(agentSelect, { target: { value: 'financial' } });
    expect(agentSelect.value).toBe('financial');
  });

  it('changing model selector updates selected model', () => {
    render(<CommandPane />);
    const modelSelect = document.querySelector('#ai-model');
    fireEvent.change(modelSelect, { target: { value: 'gpt-4' } });
    expect(modelSelect.value).toBe('gpt-4');
  });

  it('renders message list', () => {
    render(<CommandPane />);
    expect(screen.getByTestId('message-list')).toBeInTheDocument();
  });
});

describe('CommandPane with selected task', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows selected task info when selectedTask is set', async () => {
    const useStoreMod = await import('../../../store/useStore');
    const useStore = vi.mocked(useStoreMod.default);
    useStore.mockReturnValue({
      selectedTask: {
        id: 'task-1',
        title: 'My Test Task',
        status: 'pending',
        priority: 'high',
        dueDate: '2026-04-01',
      },
      tasks: [{ id: 'task-1' }],
      messages: [],
      addMessage: mockAddMessage,
      updateMessage: mockUpdateMessage,
      startExecution: mockStartExecution,
      completeExecution: mockCompleteExecution,
      failExecution: mockFailExecution,
      removeMessage: mockRemoveMessage,
    });
    render(<CommandPane />);
    expect(screen.getByText('My Test Task')).toBeInTheDocument();
  });
});
