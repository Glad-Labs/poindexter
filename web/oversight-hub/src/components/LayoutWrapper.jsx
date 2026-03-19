import logger from '@/lib/logger';
/**
 * LayoutWrapper.jsx
 *
 * Persistent layout component that wraps all pages
 * Provides:
 * - Navigation header with menu
 * - Chat panel at bottom
 * - Consistent styling across all pages
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import * as cofounderAgentClient from '../services/cofounderAgentClient';
import { modelService } from '../services/modelService';
import { composeAndExecuteTask } from '../services/naturalLanguageComposerService';
import useAuth from '../hooks/useAuth';
import ModelSelectDropdown from './ModelSelectDropdown';
import BackendStatusBanner from './BackendStatusBanner';
import '../OversightHub.css';

// Generate unique message IDs to avoid React key warnings
const generateMessageId = () => {
  return `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
};

const LayoutWrapper = ({ children }) => {
  const navigate = useNavigate();
  const chatEndRef = useRef(null);
  const chatPanelRef = useRef(null);
  const [navMenuOpen, setNavMenuOpen] = useState(false);
  const navMenuBtnRef = useRef(null);
  const [chatMessages, setChatMessages] = useState([
    {
      id: generateMessageId(),
      sender: 'system',
      text: '👋 Poindexter Assistant ready!\n\n💭 **Conversation Mode**: Direct Q&A with AI\n🔄 **Agent Mode**: Compose and execute capability tasks\n\nChoose a mode to get started!',
    },
  ]);
  const [chatInput, setChatInput] = useState('');
  const [chatMode, setChatMode] = useState('conversation');
  const [selectedModel, setSelectedModel] = useState('ollama-mistral');
  const [selectedAgent, setSelectedAgent] = useState('orchestrator');
  const [chatHeight, setChatHeight] = useState(
    parseInt(localStorage.getItem('chatHeight') || '300', 10)
  );
  const [isResizing, setIsResizing] = useState(false);
  const [ollamaConnected, setOllamaConnected] = useState(false);
  // const [availableOllamaModels, setAvailableOllamaModels] = useState([]);
  // const [selectedOllamaModel, setSelectedOllamaModel] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [, setAvailableModels] = useState([]);
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [modelsByProvider, setModelsByProvider] = useState({
    ollama: [],
    openai: [],
    anthropic: [],
    google: [],
  });

  const navigationItems = [
    { label: 'Dashboard', icon: '📊', path: 'dashboard' },
    { label: 'Tasks', icon: '✅', path: 'tasks' },
    { label: 'Content', icon: '📄', path: 'content' },
    { label: 'Approvals', icon: '👁️', path: 'approvals' },
    { label: 'Services', icon: '⚡', path: 'services' },
    { label: 'AI Studio', icon: '🤖', path: 'ai' },
    { label: 'Costs', icon: '💰', path: 'costs' },
    { label: 'Performance', icon: '⚡', path: 'performance' },
    { label: 'Settings', icon: '⚙️', path: 'settings' },
  ];

  // Models list - kept for reference, actual models loaded from API
  // const models = [
  //   {
  //     id: 'ollama-mistral',
  //     name: 'Ollama Mistral',
  //     icon: '🏠',
  //     provider: 'ollama',
  //   },
  //   { id: 'openai-gpt4', name: 'OpenAI GPT-4', icon: '🔴', provider: 'openai' },
  //   {
  //     id: 'claude-opus',
  //     name: 'Claude Opus',
  //     icon: '⭐',
  //     provider: 'anthropic',
  //   },
  //   { id: 'gemini-pro', name: 'Google Gemini', icon: '✨', provider: 'google' },
  // ];

  const agents = [
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

  // Close nav menu on Escape key press and return focus to trigger (issue #771)
  useEffect(() => {
    if (!navMenuOpen) return;
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        setNavMenuOpen(false);
        navMenuBtnRef.current?.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [navMenuOpen]);

  // Check Ollama availability on mount only in development (local dev mode)
  useEffect(() => {
    if (authLoading || !isAuthenticated) {
      setOllamaConnected(false);
      return;
    }

    // Only check Ollama in development environment
    const isDevelopment = process.env.NODE_ENV === 'development';

    if (!isDevelopment) {
      setOllamaConnected(false); // Disable in production
      return;
    }

    const checkOllama = async () => {
      try {
        const { isOllamaAvailable } = await import('../services/ollamaService');
        const available = await isOllamaAvailable();
        setOllamaConnected(available);
      } catch (error) {
        logger.debug('Error checking Ollama:', error.message);
        setOllamaConnected(false);
      }
    };

    // Check once on mount
    checkOllama();

    // Optionally check every 5 minutes in development
    const interval = setInterval(checkOllama, 300000); // 5 minutes
    return () => clearInterval(interval);
  }, [isAuthenticated, authLoading]);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages]);

  // Show mode help when switching modes (only trigger on chatMode change)
  useEffect(() => {
    const modeHelpMessage = {
      id: generateMessageId(),
      sender: 'system',
      text:
        chatMode === 'agent'
          ? "🔄 **Agent Mode Active**\n\nDescribe what you want to do, and I'll compose a capability task chain to execute it.\n\nExample: 'Write a blog post about AI trends and publish it'"
          : "💭 **Conversation Mode Active**\n\nAsk me anything! I'll respond with helpful information.",
    };

    // Only add if last message isn't already a mode help (avoid duplicates)
    setChatMessages((prev) => {
      if (
        prev.length > 0 &&
        prev[prev.length - 1]?.text === modeHelpMessage.text
      ) {
        return prev; // Already have this message
      }
      return [...prev, modeHelpMessage];
    });
  }, [chatMode]);

  // Initialize available models from API
  useEffect(() => {
    if (authLoading || !isAuthenticated) {
      const defaults = modelService.getDefaultModels();
      setAvailableModels(defaults);
      setModelsByProvider(modelService.groupModelsByProvider(defaults));
      return;
    }

    const loadModels = async () => {
      try {
        const models = await modelService.getAvailableModels(true); // Force refresh
        setAvailableModels(models);

        // Group models by provider
        const grouped = modelService.groupModelsByProvider(models);
        setModelsByProvider(grouped);

        logger.log('✅ Loaded models from API:', {
          total: models.length,
          grouped,
        });
      } catch (error) {
        logger.warn('Error loading models from API:', error);
        // Fall back to default models
        const defaults = modelService.getDefaultModels();
        setAvailableModels(defaults);
        const grouped = modelService.groupModelsByProvider(defaults);
        setModelsByProvider(grouped);
      }
    };

    loadModels();
  }, [isAuthenticated, authLoading]);

  const handleNavigate = (page) => {
    setNavMenuOpen(false);
    const routeMap = {
      dashboard: '/',
      tasks: '/tasks',
      content: '/content',
      approvals: '/approvals',
      services: '/services',
      ai: '/ai',
      costs: '/costs',
      performance: '/performance',
      settings: '/settings',
    };
    navigate(routeMap[page] || '/');
  };

  const handleSendMessage = async () => {
    if (!chatInput.trim()) return;

    const userMessage = chatInput; // Store before clearing
    const newMessage = {
      id: generateMessageId(),
      sender: 'user',
      text: userMessage,
    };

    setChatMessages([...chatMessages, newMessage]);
    setChatInput('');
    setIsLoading(true);

    try {
      // Route based on chat mode
      if (chatMode === 'agent') {
        // Agent Mode: NLP Task Composition
        await handleAgentModeMessage(userMessage);
      } else {
        // Conversation Mode: Regular chat
        await handleConversationModeMessage(userMessage);
      }
    } catch (error) {
      logger.error('Chat error:', error);
      setChatMessages((prev) => [
        ...prev,
        {
          id: generateMessageId(),
          sender: 'ai',
          text: `❌ Error: ${error.message}`,
          error: true,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleConversationModeMessage = async (userMessage) => {
    /**
     * Conversation Mode: Simple request/response with selected agent
     */
    try {
      const response = await cofounderAgentClient.sendChatMessage(
        userMessage,
        selectedModel,
        selectedAgent || 'default'
      );

      if (!response || !response.response) {
        throw new Error('Invalid response: missing response field');
      }

      setChatMessages((prev) => [
        ...prev,
        {
          id: generateMessageId(),
          sender: 'ai',
          text: response.response,
          model: response.model,
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (error) {
      throw new Error(`Chat failed: ${error.message}`);
    }
  };

  const handleAgentModeMessage = async (userMessage) => {
    /**
     * Agent Mode: NLP-powered task composition and execution
     *
     * Flow:
     * 1. Analyze user request to compose capability task
     * 2. Display composed task and confirmation
     * 3. Auto-execute the task
     * 4. Show results as they complete
     */
    try {
      // Show "analyzing" indicator
      setChatMessages((prev) => [
        ...prev,
        {
          id: generateMessageId(),
          sender: 'system',
          text: '🤔 Analyzing request and composing task chain...',
          isLoading: true,
        },
      ]);

      // Compose and execute task
      const result = await composeAndExecuteTask(userMessage, {
        saveTask: true,
      });

      // Remove loading message
      setChatMessages((prev) => prev.filter((msg) => !msg.isLoading));

      if (!result.success) {
        throw new Error(result.error || 'Failed to compose task');
      }

      // Show composed task
      const taskSummary = result.task_definition
        ? `✅ **Task Composed**: ${result.task_definition.name}\n\n` +
          `**Steps**: ${result.task_definition.steps
            .map((s) => s.capability_name)
            .join(' → ')}\n\n` +
          `${result.explanation}\n\n` +
          (result.execution_id
            ? `🚀 Executing: ${result.execution_id}`
            : '⏳ Ready to execute')
        : result.explanation;

      setChatMessages((prev) => [
        ...prev,
        {
          id: generateMessageId(),
          sender: 'ai',
          text: taskSummary,
          model: 'nlp-composer',
          task: result.task_definition,
          executionId: result.execution_id,
          isTaskComposition: true,
          timestamp: new Date().toISOString(),
        },
      ]);

      // If auto-executed, show status
      if (result.execution_id) {
        setChatMessages((prev) => [
          ...prev,
          {
            id: generateMessageId(),
            sender: 'system',
            text: `⏳ Task executing... Check Services → Capability Composer for details`,
            timestamp: new Date().toISOString(),
          },
        ]);
      }
    } catch (error) {
      throw new Error(`Task composition failed: ${error.message}`);
    }
  };

  const handleClearHistory = () => {
    setChatMessages([
      {
        id: generateMessageId(),
        sender: 'system',
        text: 'Poindexter ready. How can I help?',
      },
    ]);
  };

  // Chat resize handle
  const handleResizeStart = (e) => {
    e.preventDefault();
    setIsResizing(true);
    document.body.classList.add('resizing-chat');
    const startY = e.clientY || e.touches?.[0]?.clientY;
    const startHeight = chatHeight;

    const handleMouseMove = (moveEvent) => {
      const currentY = moveEvent.clientY || moveEvent.touches?.[0]?.clientY;
      const diff = startY - currentY; // Negative diff = bigger chat
      const newHeight = Math.max(
        150,
        Math.min(startHeight + diff, window.innerHeight * 0.8)
      );
      setChatHeight(newHeight);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      document.body.classList.remove('resizing-chat');
      localStorage.setItem('chatHeight', Math.round(chatHeight));
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('touchmove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.removeEventListener('touchend', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('touchmove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.addEventListener('touchend', handleMouseUp);
  };

  // Keyboard handler for chat panel resize handle — WCAG 2.1.1 compliance
  const handleChatResizeKeyDown = useCallback((e) => {
    const CHAT_KEYBOARD_STEP = 20;
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setChatHeight((h) =>
        Math.min(h + CHAT_KEYBOARD_STEP, window.innerHeight * 0.8)
      );
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setChatHeight((h) => Math.max(h - CHAT_KEYBOARD_STEP, 150));
    }
  }, []);

  return (
    <div className="oversight-hub-container">
      {/* Skip-to-content link — first focusable element; visually hidden until focused (WCAG 2.4.1) */}
      <a href="#main-content" className="skip-to-main">
        Skip to main content
      </a>
      <BackendStatusBanner />
      {/* Header with Navigation */}
      <header className="oversight-header">
        <div className="header-top">
          <button
            ref={navMenuBtnRef}
            className="nav-menu-btn"
            aria-label="Navigation menu"
            aria-expanded={navMenuOpen}
            aria-controls="nav-menu-dropdown"
            onClick={() => setNavMenuOpen(!navMenuOpen)}
          >
            <span aria-hidden="true">☰</span>
          </button>
          {/* Use span not h1 — page route components define the single h1 (WCAG 1.3.1) */}
          <span className="oversight-hub-title" aria-label="Oversight Hub">
            <span aria-hidden="true">🎛️</span> Oversight Hub
          </span>
        </div>
        <div className="header-status">
          <span
            role="status"
            aria-live="polite"
            aria-label={ollamaConnected ? 'Ollama connected' : 'Ollama offline'}
          >
            <span aria-hidden="true">{ollamaConnected ? '🟢' : '🔴'}</span>{' '}
            {ollamaConnected ? 'Ollama Ready' : 'Ollama Offline'}
          </span>
        </div>
      </header>

      {/* Navigation Menu */}
      <nav
        id="nav-menu-dropdown"
        className={`nav-menu-dropdown${navMenuOpen ? '' : ' nav-menu-hidden'}`}
        aria-label="Main navigation"
      >
        {navMenuOpen && <div className="nav-menu-header">Navigation</div>}
        {navMenuOpen &&
          navigationItems.map((item) => (
            <button
              key={item.path}
              className="nav-menu-item"
              onClick={() => handleNavigate(item.path)}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                width: '100%',
                textAlign: 'left',
                padding: '0.75rem 1rem',
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
                color: 'var(--text-primary)',
                borderLeft: '3px solid var(--border-secondary)',
              }}
            >
              <span className="nav-menu-icon" aria-hidden="true">
                {item.icon}
              </span>
              <span className="nav-menu-label">{item.label}</span>
            </button>
          ))}
      </nav>

      {/* Main Content Area */}
      <div className="oversight-hub-layout">
        <main id="main-content" className="main-panel">
          {children}
        </main>

        {/* Chat Panel Resize Handle — keyboard-accessible (WCAG 2.1.1) */}
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize chat panel. Use Arrow Up and Arrow Down keys to adjust height."
          aria-valuenow={Math.round(chatHeight)}
          aria-valuemin={150}
          aria-valuemax={Math.round(window.innerHeight * 0.8)}
          tabIndex={0}
          className={`chat-resize-handle ${isResizing ? 'resizing' : ''}`}
          onMouseDown={handleResizeStart}
          onTouchStart={handleResizeStart}
          onKeyDown={handleChatResizeKeyDown}
          title="Drag to resize chat panel"
        >
          <span className="drag-indicator">⋮⋮</span>
        </div>

        {/* Chat Panel */}
        <div
          ref={chatPanelRef}
          className="chat-panel"
          style={{
            height: `${chatHeight}px`,
          }}
        >
          <div className="chat-header">
            <span>💬 Poindexter Assistant</span>
            <div className="chat-mode-toggle">
              <button
                className={`mode-btn ${chatMode === 'conversation' ? 'active' : ''}`}
                onClick={() => setChatMode('conversation')}
                aria-pressed={chatMode === 'conversation'}
              >
                💭 Conversation
              </button>
              <button
                className={`mode-btn ${chatMode === 'agent' ? 'active' : ''}`}
                onClick={() => setChatMode('agent')}
                aria-pressed={chatMode === 'agent'}
              >
                🔄 Agent
              </button>
            </div>
            <ModelSelectDropdown
              value={selectedModel}
              onChange={(value) => setSelectedModel(value)}
              modelsByProvider={modelsByProvider}
              className="model-selector"
            />
            {chatMode === 'agent' && (
              <select
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
                aria-label="Select agent"
              >
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Messages */}
          <div className="chat-messages">
            {chatMessages.map((msg) => (
              <div key={msg.id} className={`message message-${msg.sender}`}>
                <div className="message-avatar">
                  {msg.sender === 'user'
                    ? '👤'
                    : msg.sender === 'system'
                      ? '⚙️'
                      : '🤖'}
                </div>
                <div className="message-content">
                  {msg.error && (
                    <div className="message-error">⚠️ {msg.text}</div>
                  )}
                  {msg.isTaskComposition && (
                    <div className="message-task-composition">
                      {/* Render markdown-style text */}
                      {msg.text.split('\n\n').map((paragraph, idx) => (
                        <div
                          key={`${msg.id}-para-${idx}`}
                          style={{ marginBottom: '8px' }}
                        >
                          {paragraph.split('\n').map((line, lineIdx) => {
                            // Bold for **text**
                            const boldRegex = /\*\*([^*]+)\*\*/g;
                            const parts = [];
                            let lastIndex = 0;

                            line.replace(
                              boldRegex,
                              (match, content, offset) => {
                                if (offset > lastIndex) {
                                  parts.push(
                                    <span key={`text-${lastIndex}`}>
                                      {line.substring(lastIndex, offset)}
                                    </span>
                                  );
                                }
                                parts.push(
                                  <strong key={`bold-${offset}`}>
                                    {content}
                                  </strong>
                                );
                                lastIndex = offset + match.length;
                                return match;
                              }
                            );

                            if (lastIndex < line.length) {
                              parts.push(
                                <span key={`text-end`}>
                                  {line.substring(lastIndex)}
                                </span>
                              );
                            }

                            return (
                              <div
                                key={`${msg.id}-line-${lineIdx}`}
                                style={{ marginBottom: '4px' }}
                              >
                                {parts.length > 0 ? parts : line}
                              </div>
                            );
                          })}
                        </div>
                      ))}
                      {msg.executionId && (
                        <div
                          style={{
                            marginTop: '12px',
                            fontSize: '12px',
                            color: '#999',
                          }}
                        >
                          Execution ID: {msg.executionId}
                        </div>
                      )}
                    </div>
                  )}
                  {!msg.error && !msg.isTaskComposition && <p>{msg.text}</p>}
                </div>
              </div>
            ))}
            {isLoading && (
              <div
                className="message message-ai"
                role="status"
                aria-label="AI is typing"
                aria-live="polite"
              >
                <div className="message-avatar" aria-hidden="true">
                  🤖
                </div>
                <div className="message-content">
                  <div className="typing-indicator" aria-hidden="true">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  {/* sr-only text is announced once when the element appears */}
                  <span className="sr-only">AI is typing a response</span>
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Chat Input */}
          <div className="chat-input-area">
            <input
              className="chat-input"
              type="text"
              value={chatInput}
              aria-label={
                chatMode === 'agent'
                  ? 'Describe a task for the agent'
                  : 'Ask Poindexter a question'
              }
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !isLoading) {
                  handleSendMessage();
                }
              }}
              placeholder={
                chatMode === 'agent'
                  ? 'Describe a task (e.g., "Write a blog post about AI and publish it")...'
                  : 'Ask Poindexter...'
              }
              disabled={isLoading}
            />
            <button
              onClick={handleSendMessage}
              disabled={!chatInput.trim() || isLoading}
              aria-label={
                chatMode === 'agent'
                  ? 'Compose and execute task'
                  : 'Send message'
              }
            >
              {chatMode === 'agent' ? '⚡' : '📤'}
            </button>
            <button
              onClick={handleClearHistory}
              aria-label="Clear chat history"
            >
              🗑️
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LayoutWrapper;
