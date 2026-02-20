/**
 * LayoutWrapper.jsx
 *
 * Persistent layout component that wraps all pages
 * Provides:
 * - Navigation header with menu
 * - Chat panel at bottom
 * - Consistent styling across all pages
 */

import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import * as cofounderAgentClient from '../services/cofounderAgentClient';
import { modelService } from '../services/modelService';
import { composeAndExecuteTask } from '../services/naturalLanguageComposerService';
import ModelSelectDropdown from './ModelSelectDropdown';
import '../OversightHub.css';

const LayoutWrapper = ({ children }) => {
  const navigate = useNavigate();
  const chatEndRef = useRef(null);
  const chatPanelRef = useRef(null);
  const [navMenuOpen, setNavMenuOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState([
    {
      id: 1,
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

  // Check Ollama availability on mount only in development (local dev mode)
  useEffect(() => {
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
        console.debug('Error checking Ollama:', error.message);
        setOllamaConnected(false);
      }
    };

    // Check once on mount
    checkOllama();

    // Optionally check every 5 minutes in development
    const interval = setInterval(checkOllama, 300000); // 5 minutes
    return () => clearInterval(interval);
  }, []);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages]);

  // Show mode help when switching modes
  useEffect(() => {
    if (chatMessages.length === 0) return; // Skip on initial load

    const modeHelpMessage = {
      id: chatMessages.length + 1,
      sender: 'system',
      text:
        chatMode === 'agent'
          ? "🔄 **Agent Mode Active**\n\nDescribe what you want to do, and I'll compose a capability task chain to execute it.\n\nExample: 'Write a blog post about AI trends and publish it'"
          : "💭 **Conversation Mode Active**\n\nAsk me anything! I'll respond with helpful information.",
    };

    // Only add if last message isn't already a mode help
    if (chatMessages[chatMessages.length - 1]?.text !== modeHelpMessage.text) {
      setChatMessages((prev) => [...prev, modeHelpMessage]);
    }
  }, [chatMode]);

  // Initialize available models from API
  useEffect(() => {
    const loadModels = async () => {
      try {
        const models = await modelService.getAvailableModels(true); // Force refresh
        setAvailableModels(models);

        // Group models by provider
        const grouped = modelService.groupModelsByProvider(models);
        setModelsByProvider(grouped);

        console.log('✅ Loaded models from API:', {
          total: models.length,
          grouped,
        });
      } catch (error) {
        console.warn('Error loading models from API:', error);
        // Fall back to default models
        const defaults = modelService.getDefaultModels();
        setAvailableModels(defaults);
        const grouped = modelService.groupModelsByProvider(defaults);
        setModelsByProvider(grouped);
      }
    };

    loadModels();
  }, []);

  const handleNavigate = (page) => {
    setNavMenuOpen(false);
    const routeMap = {
      dashboard: '/',
      tasks: '/tasks',
      content: '/content',
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
      id: chatMessages.length + 1,
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
        await handleAgentModeMessage(userMessage, chatMessages.length + 1);
      } else {
        // Conversation Mode: Regular chat
        await handleConversationModeMessage(
          userMessage,
          chatMessages.length + 1
        );
      }
    } catch (error) {
      console.error('Chat error:', error);
      setChatMessages((prev) => [
        ...prev,
        {
          id: prev.length + 1,
          sender: 'ai',
          text: `❌ Error: ${error.message}`,
          error: true,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleConversationModeMessage = async (userMessage, nextId) => {
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
          id: nextId + 1,
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

  const handleAgentModeMessage = async (userMessage, nextId) => {
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
          id: nextId + 1,
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
          id: nextId + 1,
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
            id: prev.length + 1,
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
      { id: 1, sender: 'system', text: 'Poindexter ready. How can I help?' },
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

  return (
    <div className="oversight-hub-container">
      {/* Header with Navigation */}
      <header className="oversight-header">
        <div className="header-top">
          <button
            className="nav-menu-btn"
            onClick={() => setNavMenuOpen(!navMenuOpen)}
          >
            ☰
          </button>
          <h1>🎛️ Oversight Hub</h1>
        </div>
        <div className="header-status">
          {ollamaConnected ? '🟢 Ollama Ready' : '🔴 Ollama Offline'}
        </div>
      </header>

      {/* Navigation Menu */}
      {navMenuOpen && (
        <div className="nav-menu-dropdown">
          <div className="nav-menu-header">Navigation</div>
          {navigationItems.map((item) => (
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
              <span className="nav-menu-icon">{item.icon}</span>
              <span className="nav-menu-label">{item.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Main Content Area */}
      <div className="oversight-hub-layout">
        <div className="main-panel">{children}</div>

        {/* Chat Panel Resize Handle */}
        <div
          className={`chat-resize-handle ${isResizing ? 'resizing' : ''}`}
          onMouseDown={handleResizeStart}
          onTouchStart={handleResizeStart}
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
              >
                💭 Conversation
              </button>
              <button
                className={`mode-btn ${chatMode === 'agent' ? 'active' : ''}`}
                onClick={() => setChatMode('agent')}
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
                        <div key={idx} style={{ marginBottom: '8px' }}>
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
                                key={lineIdx}
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
              <div className="message message-ai">
                <div className="message-avatar">🤖</div>
                <div className="message-content">
                  <div className="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
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
              onChange={(e) => setChatInput(e.target.value)}
              onKeyPress={(e) => {
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
              title={
                chatMode === 'agent'
                  ? 'Compose and execute task'
                  : 'Send message'
              }
            >
              {chatMode === 'agent' ? '⚡' : '📤'}
            </button>
            <button onClick={handleClearHistory} title="Clear history">
              🗑️
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LayoutWrapper;
