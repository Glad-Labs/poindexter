import logger from '@/lib/logger';
import React, { useState, useRef, useCallback } from 'react';
import {
  MainContainer,
  ChatContainer,
  MessageList,
  MessageInput,
  TypingIndicator,
  Message,
} from '@chatscope/chat-ui-kit-react';
import '@chatscope/chat-ui-kit-styles/dist/default/styles.min.css';
import '../CommandPane.css';

import { useShallow } from 'zustand/react/shallow';
import useStore from '../../store/useStore';
import { MESSAGE_TYPES } from '../../lib/messageTypes';
import OrchestratorCommandMessage from '../OrchestratorCommandMessage';
import OrchestratorStatusMessage from '../OrchestratorStatusMessage';
import OrchestratorResultMessage from '../OrchestratorResultMessage';
import OrchestratorErrorMessage from '../OrchestratorErrorMessage';

// Available AI Models
const AI_MODELS = [
  { id: 'gpt-4', name: 'GPT-4 (Advanced)' },
  { id: 'gpt-3.5', name: 'GPT-3.5 (Fast)' },
  { id: 'claude-3', name: 'Claude 3 (Balanced)' },
  { id: 'local', name: 'Local Model' },
];

// Available Agents for delegation
const AVAILABLE_AGENTS = [
  {
    id: 'content',
    name: '📝 Content Agent',
    description: 'Generate and manage content',
  },
  {
    id: 'financial',
    name: '📊 Financial Agent',
    description: 'Business metrics & analysis',
  },
  {
    id: 'market',
    name: '🔍 Market Insight Agent',
    description: 'Market analysis & trends',
  },
  {
    id: 'compliance',
    name: '✓ Compliance Agent',
    description: 'Legal & regulatory checks',
  },
  {
    id: 'orchestrator',
    name: '🧠 Co-Founder Orchestrator',
    description: 'Multi-agent orchestration',
  },
];

// Command type configurations
const COMMAND_CONFIGS = {
  content_generation: {
    name: 'Content Generation',
    emoji: '📝',
    type: 'content_generation',
    phases: ['Research', 'Planning', 'Writing', 'Review', 'Publishing'],
  },
  financial_analysis: {
    name: 'Financial Analysis',
    emoji: '📊',
    type: 'financial_analysis',
    phases: ['Data Collection', 'Analysis', 'Modeling', 'Reporting'],
  },
  market_research: {
    name: 'Market Research',
    emoji: '🔍',
    type: 'market_research',
    phases: ['Gathering', 'Analysis', 'Insights', 'Reporting'],
  },
  compliance_check: {
    name: 'Compliance Check',
    emoji: '✓',
    type: 'compliance_check',
    phases: ['Scanning', 'Analysis', 'Risk Assessment', 'Report'],
  },
};

const CommandPane = () => {
  const {
    selectedTask,
    tasks,
    messages,
    addMessage,
    updateMessage,
    startExecution,
    completeExecution,
    failExecution,
    removeMessage,
  } = useStore(
    useShallow((s) => ({
      selectedTask: s.selectedTask,
      tasks: s.tasks,
      messages: s.messages,
      addMessage: s.addMessage,
      updateMessage: s.updateMessage,
      startExecution: s.startExecution,
      completeExecution: s.completeExecution,
      failExecution: s.failExecution,
      removeMessage: s.removeMessage,
    }))
  );
  const isResizing = useRef(false);
  const [isTyping, setIsTyping] = useState(false);
  const [selectedModel, setSelectedModel] = useState('ollama-mistral');
  const [selectedAgent, setSelectedAgent] = useState('orchestrator');
  const [showContext, setShowContext] = useState(false);
  const [delegateMode, setDelegateMode] = useState(false);

  /**
   * Parse user input and create command message
   */
  const parseUserCommand = useCallback(
    (input) => {
      // Simple command detection (can be enhanced with AI parsing)
      const lowerInput = input.toLowerCase();
      let commandType = 'content_generation'; // default

      if (lowerInput.includes('financial') || lowerInput.includes('cost')) {
        commandType = 'financial_analysis';
      } else if (
        lowerInput.includes('market') ||
        lowerInput.includes('research')
      ) {
        commandType = 'market_research';
      } else if (
        lowerInput.includes('compliance') ||
        lowerInput.includes('legal')
      ) {
        commandType = 'compliance_check';
      }

      const config = COMMAND_CONFIGS[commandType];
      return {
        name: input.substring(0, 50), // First 50 chars as command name
        type: commandType,
        description: input,
        parameters: {
          userInput: input,
          model: selectedModel,
          agent: selectedAgent,
          context: selectedTask ? selectedTask.title : 'No active task',
        },
        config,
      };
    },
    [selectedModel, selectedAgent, selectedTask]
  );

  /**
   * Handle result approval
   */
  const handleApproveResult = useCallback(
    (resultMessage, feedback) => {
      const approvalMessage = {
        type: 'text',
        direction: 'incoming',
        sender: 'AI',
        message: `✓ Result approved${feedback ? ': ' + feedback : ''}`,
      };
      addMessage(approvalMessage);
    },
    [addMessage]
  );

  /**
   * Handle result rejection
   */
  const handleRejectResult = useCallback(
    (resultMessage, feedback) => {
      const rejectionMessage = {
        type: 'text',
        direction: 'incoming',
        sender: 'AI',
        message: `✗ Result rejected${feedback ? ': ' + feedback : ''}. Would you like me to regenerate?`,
      };
      addMessage(rejectionMessage);
    },
    [addMessage]
  );

  /**
   * Handle retry after error
   */
  const handleRetryCommand = useCallback(
    (_errorMessage) => {
      const retryMessage = {
        type: 'text',
        direction: 'incoming',
        sender: 'AI',
        message: 'Retrying command...',
      };
      addMessage(retryMessage);
      // In real scenario, would re-execute the command
    },
    [addMessage]
  );

  /**
   * Execute command from OrchestratorCommandMessage
   */
  const handleExecuteCommand = useCallback(
    async (commandMessage, params) => {
      const executionId = `exec-${Date.now()}`;
      const commandType = commandMessage.commandType || 'content_generation';
      const config =
        COMMAND_CONFIGS[commandType] || COMMAND_CONFIGS.content_generation;

      // Start execution in store
      startExecution(executionId, commandType, config.phases);

      // Add status message to stream
      const statusMessage = {
        type: 'status',
        direction: 'incoming',
        sender: 'AI',
        executionId,
        progress: 0,
        phases: config.phases,
        currentPhaseIndex: 0,
        phaseBreakdown: config.phases.reduce((acc, phase) => {
          acc[phase] = Math.round(100 / config.phases.length);
          return acc;
        }, {}),
      };
      addMessage(statusMessage);
      const statusMessageIndex = messages.length; // Track for updates

      setIsTyping(true);

      try {
        const { makeRequest } =
          await import('../../services/cofounderAgentClient');
        const result = await makeRequest(
          '/api/command/execute',
          'POST',
          {
            command: commandMessage.description || 'Execute command',
            parameters: params || commandMessage.parameters,
            task: selectedTask || null,
            model: selectedModel,
            agent: selectedAgent,
            context: {
              currentPage: window.location.pathname,
              selectedTaskId: selectedTask?.id || null,
              totalTasks: tasks?.length || 0,
            },
          },
          false,
          null,
          30000 // 30 second timeout for command execution
        );

        if (result.error) {
          throw new Error(result.error || 'Command execution failed');
        }

        // Simulate progress updates (in real scenario, backend would stream this)
        for (let i = 1; i < config.phases.length; i++) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          const progress = Math.round(((i + 1) / config.phases.length) * 100);
          updateMessage(statusMessageIndex, {
            progress,
            currentPhaseIndex: i,
          });
        }

        // Add result message
        const resultMessage = {
          type: 'result',
          direction: 'incoming',
          sender: 'AI',
          executionId,
          result: result.response || result.result || result,
          resultPreview: (
            result.response ||
            result.result ||
            JSON.stringify(result)
          ).substring(0, 200),
          metadata: {
            wordCount: (result.response || result.result || '').split(' ')
              .length,
            qualityScore: 8.5,
            cost: 0.35,
            executionTime: new Date().getTime(),
          },
        };
        addMessage(resultMessage);
        completeExecution(resultMessage);
      } catch (error) {
        logger.error('Error executing command:', error);
        const errorMessage = {
          type: 'error',
          direction: 'incoming',
          sender: 'AI',
          executionId,
          error: error.message || 'Command execution failed',
          severity: 'error',
          details: {
            phase: commandType,
            timestamp: new Date().toISOString(),
            code: error.code || 'EXECUTION_ERROR',
            source: 'CommandPane',
          },
          suggestions: [
            'Check backend connection at http://localhost:8000',
            'Verify API configuration in settings',
            'Try a different AI model',
            'Retry the command',
          ],
        };
        addMessage(errorMessage);
        failExecution(error);
      } finally {
        setIsTyping(false);
      }
    },
    [
      addMessage,
      updateMessage,
      startExecution,
      completeExecution,
      failExecution,
      selectedTask,
      tasks,
      selectedModel,
      selectedAgent,
      messages,
    ]
  );

  /**
   * Render messages with appropriate component type
   */
  const renderMessage = useCallback(
    (message, index) => {
      try {
        switch (message.type) {
          case MESSAGE_TYPES.ORCHESTRATOR_COMMAND:
          case 'command':
            return (
              <OrchestratorCommandMessage
                key={message.id || index}
                message={message}
                onExecute={(params) => handleExecuteCommand(message, params)}
                onCancel={() => removeMessage(index)}
              />
            );
          case MESSAGE_TYPES.ORCHESTRATOR_STATUS:
          case 'status':
            return (
              <OrchestratorStatusMessage
                key={message.id || index}
                message={message}
              />
            );
          case MESSAGE_TYPES.ORCHESTRATOR_RESULT:
          case 'result':
            return (
              <OrchestratorResultMessage
                key={message.id || index}
                message={message}
                onApprove={(feedback) => handleApproveResult(message, feedback)}
                onReject={(feedback) => handleRejectResult(message, feedback)}
              />
            );
          case MESSAGE_TYPES.ORCHESTRATOR_ERROR:
          case 'error':
            return (
              <OrchestratorErrorMessage
                key={message.id || index}
                message={message}
                onRetry={() => handleRetryCommand(message)}
                onCancel={() => removeMessage(index)}
              />
            );
          default:
            // Fallback to plain chat message
            return <Message key={message.id || index} model={message} />;
        }
      } catch (error) {
        logger.error('Error rendering message:', error);
        return null;
      }
    },
    [
      handleExecuteCommand,
      handleApproveResult,
      handleRejectResult,
      handleRetryCommand,
      removeMessage,
    ]
  );

  const handleResize = useCallback((e) => {
    if (!isResizing.current) return;

    const containerRect = document
      .querySelector('.oversight-hub-layout')
      ?.getBoundingClientRect();
    if (!containerRect) return;

    const newWidth = containerRect.right - e.clientX;

    if (newWidth >= 300 && newWidth <= 600) {
      document.documentElement.style.setProperty(
        '--command-pane-width',
        `${newWidth}px`
      );
    }
  }, []);

  const stopResize = useCallback(() => {
    isResizing.current = false;
    document.removeEventListener('mousemove', handleResize);
    document.removeEventListener('mouseup', stopResize);
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }, [handleResize]);

  const startResize = useCallback(
    (_e) => {
      isResizing.current = true;
      document.addEventListener('mousemove', handleResize);
      document.addEventListener('mouseup', stopResize);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [handleResize, stopResize]
  );

  const handleSend = async (input) => {
    // Add user message
    const userMessage = {
      type: 'text',
      message: input,
      direction: 'outgoing',
      sender: 'user',
    };
    addMessage(userMessage);

    // Parse command
    const command = parseUserCommand(input);

    // Create command message
    const commandMessage = {
      type: 'command',
      direction: 'incoming',
      sender: 'AI',
      commandName: command.name,
      commandType: command.type,
      description: command.description,
      parameters: command.parameters,
      emoji: command.config.emoji,
    };
    addMessage(commandMessage);
  };

  const handleDelegateTask = () => {
    if (!delegateMode) {
      setDelegateMode(true);
      const delegateMessage = {
        type: 'text',
        message:
          "I'm ready to help delegate tasks. What would you like me to handle?",
        direction: 'incoming',
        sender: 'AI',
      };
      addMessage(delegateMessage);
    } else {
      setDelegateMode(false);
    }
  };

  return (
    <div className="command-pane">
      <div
        className="resize-handle command-pane-resize-handle"
        onMouseDown={startResize}
      />

      {/* Header with Mode, Model Selector and Context */}
      <div className="command-pane-header">
        <div className="command-pane-top">
          <h2 className="command-pane-title">Poindexter</h2>

          {/* Orchestration Mode Selector */}
          <div className="mode-selector">
            <button
              className={`mode-btn ${delegateMode ? 'inactive' : 'active'}`}
              onClick={() => setDelegateMode(false)}
              title="Conversation Mode - Direct chat with AI"
            >
              💬 Conversation
            </button>
            <button
              className={`mode-btn ${delegateMode ? 'active' : 'inactive'}`}
              onClick={() => setDelegateMode(true)}
              title="Agentic Mode - Task delegation to agents"
            >
              🤖 Agentic
            </button>
          </div>

          <button
            className="context-toggle-btn"
            onClick={() => setShowContext(!showContext)}
            title="Toggle context information"
          >
            {showContext ? '✕' : '⊕'} Context
          </button>
        </div>

        {/* Agent Selector */}
        <div className="agent-selector">
          <label htmlFor="ai-agent" className="agent-label">
            Agent:
          </label>
          <select
            id="ai-agent"
            className="agent-dropdown"
            value={selectedAgent}
            onChange={(e) => setSelectedAgent(e.target.value)}
          >
            {AVAILABLE_AGENTS.map((agent) => (
              <option key={agent.id} value={agent.id} title={agent.description}>
                {agent.name}
              </option>
            ))}
          </select>
        </div>

        {/* Model Selector */}
        <div className="model-selector">
          <label htmlFor="ai-model" className="model-label">
            Model:
          </label>
          <select
            id="ai-model"
            className="model-dropdown"
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
          >
            {AI_MODELS.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
        </div>

        {/* Task Delegation Button */}
        <button
          className={`delegate-btn ${delegateMode ? 'active' : ''}`}
          onClick={handleDelegateTask}
          title="Delegate tasks to Poindexter"
        >
          📋 Delegate Task
        </button>
      </div>

      {/* Context Panel */}
      {showContext && (
        <div className="context-panel">
          <h3 className="context-title">Current Context</h3>
          <div className="context-item">
            <span className="context-label">Current Page:</span>
            <span className="context-value">
              {window.location.pathname || '/'}
            </span>
          </div>
          {selectedTask && (
            <>
              <div className="context-item">
                <span className="context-label">Active Task:</span>
                <span className="context-value">{selectedTask.title}</span>
              </div>
              <div className="context-item">
                <span className="context-label">Status:</span>
                <span
                  className={`status-badge status-${selectedTask.status?.toLowerCase()}`}
                >
                  {selectedTask.status}
                </span>
              </div>
            </>
          )}
          <div className="context-item">
            <span className="context-label">Total Tasks:</span>
            <span className="context-value">{tasks?.length || 0}</span>
          </div>
          <div className="context-item">
            <span className="context-label">AI Model:</span>
            <span className="context-value">
              {AI_MODELS.find((m) => m.id === selectedModel)?.name}
            </span>
          </div>
        </div>
      )}

      {/* Optional: Selected Task Display */}
      {selectedTask && (
        <div
          className="p-4 border-b"
          style={{ borderColor: 'var(--border-primary)' }}
        >
          <h2
            className="text-lg font-semibold"
            style={{ color: 'var(--text-primary)' }}
          >
            {selectedTask.title}
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Status: {selectedTask.status}
          </p>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Priority: {selectedTask.priority}
          </p>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Due Date: {selectedTask.dueDate}
          </p>
        </div>
      )}

      {/* Chat Container with Message Stream Integration */}
      <MainContainer>
        <ChatContainer>
          <MessageList
            typingIndicator={
              isTyping ? (
                <TypingIndicator content="AI is processing..." />
              ) : null
            }
          >
            {messages.map((message, i) => renderMessage(message, i))}
          </MessageList>
          <MessageInput
            placeholder="Ask for help, delegate tasks, or request analysis..."
            onSend={handleSend}
          />
        </ChatContainer>
      </MainContainer>
    </div>
  );
};

export default CommandPane;
