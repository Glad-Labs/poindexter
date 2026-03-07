import logger from '@/lib/logger';
/**
 * messageTypes.js
 *
 * Defines the message type system for the unified chat interface.
 * These types determine how messages are rendered, routed, and handled.
 *
 * Message Flow:
 * 1. User types message in CommandPane
 * 2. OrchestratorChatHandler analyzes intent
 * 3. Message assigned a type based on mode and intent
 * 4. MessageRouter component selects renderer based on type
 * 5. Appropriate message component renders the message
 */

// ============================================================================
// MESSAGE TYPE CONSTANTS
// ============================================================================

export const MESSAGE_TYPES = {
  // Conversation messages (existing Poindexter behavior)
  USER_MESSAGE: 'user_message', // User input in conversation
  AI_MESSAGE: 'ai_message', // LLM response in conversation

  // Orchestrator messages (new for integrated interface)
  ORCHESTRATOR_COMMAND: 'orchestrator_command', // User command to execute
  ORCHESTRATOR_STATUS: 'orchestrator_status', // Real-time phase updates
  ORCHESTRATOR_RESULT: 'orchestrator_result', // Final output with approval
  ORCHESTRATOR_ERROR: 'orchestrator_error', // Error with recovery
};

// ============================================================================
// MESSAGE TYPE DESCRIPTIONS
// ============================================================================

export const MESSAGE_TYPE_DESCRIPTIONS = {
  [MESSAGE_TYPES.USER_MESSAGE]: {
    label: 'User Message',
    direction: 'outgoing',
    renderer: 'DefaultUserMessage',
    hasActions: false,
    example: 'Generate a blog post about AI trends',
  },
  [MESSAGE_TYPES.AI_MESSAGE]: {
    label: 'AI Response',
    direction: 'incoming',
    renderer: 'DefaultAIMessage',
    hasActions: false,
    example: 'Here is the blog post...',
  },
  [MESSAGE_TYPES.ORCHESTRATOR_COMMAND]: {
    label: 'Orchestrator Command',
    direction: 'outgoing',
    renderer: 'OrchestratorCommandMessage',
    hasActions: true,
    actions: ['execute', 'cancel', 'edit'],
    example: 'Generate blog post about AI trends (2000 words, professional)',
  },
  [MESSAGE_TYPES.ORCHESTRATOR_STATUS]: {
    label: 'Orchestrator Status',
    direction: 'incoming',
    renderer: 'OrchestratorStatusMessage',
    hasActions: false,
    example: 'Phase 2/6: Creating content outline (33% complete, ETA: 3 min)',
  },
  [MESSAGE_TYPES.ORCHESTRATOR_RESULT]: {
    label: 'Orchestrator Result',
    direction: 'incoming',
    renderer: 'OrchestratorResultMessage',
    hasActions: true,
    actions: ['approve', 'reject', 'edit', 'export', 'copy'],
    example: 'Blog Post Complete - 2847 words, 4 images, Quality: 94%',
  },
  [MESSAGE_TYPES.ORCHESTRATOR_ERROR]: {
    label: 'Orchestrator Error',
    direction: 'incoming',
    renderer: 'OrchestratorErrorMessage',
    hasActions: true,
    actions: ['retry', 'cancel', 'view_details'],
    example: 'Error: Model unavailable. Try a different host.',
  },
};

// ============================================================================
// MESSAGE STRUCTURE SCHEMAS
// ============================================================================

/**
 * Standard message structure for USER_MESSAGE
 */
export const USER_MESSAGE_SCHEMA = {
  type: MESSAGE_TYPES.USER_MESSAGE,
  message: 'string', // User input text
  sender: 'user', // Always 'user'
  direction: 'outgoing', // Always outgoing
  timestamp: 'Date ISO string',
  metadata: {
    mode: 'agent|conversation', // Current mode when sent
  },
};

/**
 * Standard message structure for AI_MESSAGE
 */
export const AI_MESSAGE_SCHEMA = {
  type: MESSAGE_TYPES.AI_MESSAGE,
  message: 'string', // AI response text
  sender: 'AI', // Always 'AI'
  direction: 'incoming', // Always incoming
  timestamp: 'Date ISO string',
  metadata: {
    model: 'string', // Which model generated response
    host: 'string', // Which host was used
    tokensUsed: 'number',
    processingTime: 'number (ms)',
  },
};

/**
 * Standard message structure for ORCHESTRATOR_COMMAND
 */
export const ORCHESTRATOR_COMMAND_SCHEMA = {
  type: MESSAGE_TYPES.ORCHESTRATOR_COMMAND,
  originalMessage: 'string', // User input before parsing
  sender: 'user', // Always 'user'
  direction: 'outgoing', // Always outgoing
  commandType: 'string', // generate, analyze, optimize, plan, export, delegate
  parameters: {
    rawInput: 'string',
    topic: 'string',
    style: 'string', // professional, casual, academic, etc.
    length: 'string', // short, medium, long, or "2000 words"
    format: 'string', // markdown, html, json, pdf, etc.
    context: 'string', // Additional context
    additionalInstructions: 'string',
  },
  timestamp: 'Date ISO string',
  status: 'pending', // pending, executing, completed, failed
  executionId: 'string|null', // Set after submission
};

/**
 * Standard message structure for ORCHESTRATOR_STATUS
 */
export const ORCHESTRATOR_STATUS_SCHEMA = {
  type: MESSAGE_TYPES.ORCHESTRATOR_STATUS,
  sender: 'orchestrator', // Always 'orchestrator'
  direction: 'incoming', // Always incoming
  executionId: 'string', // Links to command that initiated it
  phase: 'string', // Current phase name (e.g., "Research", "Writing")
  phaseNumber: 'number', // Current phase (e.g., 2)
  totalPhases: 'number', // Total phases (e.g., 6)
  progress: 'number', // 0-100 percentage
  currentTask: 'string', // What the agent is currently doing
  estimatedTimeRemaining: 'number|null', // Seconds remaining
  timestamp: 'Date ISO string',
  metadata: {
    // Any additional status data from backend
  },
};

/**
 * Standard message structure for ORCHESTRATOR_RESULT
 */
export const ORCHESTRATOR_RESULT_SCHEMA = {
  type: MESSAGE_TYPES.ORCHESTRATOR_RESULT,
  sender: 'orchestrator', // Always 'orchestrator'
  direction: 'incoming', // Always incoming
  executionId: 'string', // Links to command that produced it
  content: 'string', // Main result content
  title: 'string', // Title of result (e.g., "Blog Post: AI Trends 2025")
  summary: 'string', // Brief summary of result
  metadata: {
    wordCount: 'number',
    imageCount: 'number',
    qualityScore: 'number', // 0-100
    generationTime: 'number (ms)',
    cost: 'number', // In USD
    model: 'string', // Which model was used
    host: 'string', // Which host was used
  },
  canApprove: true,
  canReject: true,
  canEdit: true,
  canExport: true,
  timestamp: 'Date ISO string',
};

/**
 * Standard message structure for ORCHESTRATOR_ERROR
 */
export const ORCHESTRATOR_ERROR_SCHEMA = {
  type: MESSAGE_TYPES.ORCHESTRATOR_ERROR,
  sender: 'orchestrator', // Always 'orchestrator'
  direction: 'incoming', // Always incoming
  executionId: 'string|null', // Links to command if it failed mid-execution
  error: 'string', // Error message
  errorType: 'string', // RATE_LIMIT, TIMEOUT, MODEL_UNAVAILABLE, etc.
  suggestion: 'string', // Recovery suggestion for user
  timestamp: 'Date ISO string',
  canRetry: 'boolean', // Whether user can retry
  canCancel: true, // Always can cancel
};

// ============================================================================
// MESSAGE ROUTER COMPONENT
// ============================================================================

/**
 * Maps message types to their renderer components
 * This object is used by the MessageRouter to select appropriate component
 */
export const MESSAGE_RENDERERS = {
  [MESSAGE_TYPES.USER_MESSAGE]: 'DefaultUserMessage',
  [MESSAGE_TYPES.AI_MESSAGE]: 'DefaultAIMessage',
  [MESSAGE_TYPES.ORCHESTRATOR_COMMAND]: 'OrchestratorCommandMessage',
  [MESSAGE_TYPES.ORCHESTRATOR_STATUS]: 'OrchestratorStatusMessage',
  [MESSAGE_TYPES.ORCHESTRATOR_RESULT]: 'OrchestratorResultMessage',
  [MESSAGE_TYPES.ORCHESTRATOR_ERROR]: 'OrchestratorErrorMessage',
};

/**
 * Message Router Component
 * Selects and renders appropriate message component based on message type
 *
 * Usage:
 * <MessageRouter message={messageObject} onAction={handleMessageAction} />
 */
export const MessageRouter = ({ message, onAction }) => {
  if (!message || !message.type) {
    logger.warn('MessageRouter: Invalid message object', message);
    return null;
  }

  const rendererName = MESSAGE_RENDERERS[message.type];

  if (!rendererName) {
    logger.warn(`MessageRouter: Unknown message type "${message.type}"`);
    return null;
  }

  // Renderer mapping (these components are imported separately)
  // This is a routing table - actual components imported in CommandPane
  return {
    rendererName,
    message,
    onAction,
  };
};

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Check if a message type is an orchestrator message
 */
export const isOrchestratorMessage = (messageType) => {
  return [
    MESSAGE_TYPES.ORCHESTRATOR_COMMAND,
    MESSAGE_TYPES.ORCHESTRATOR_STATUS,
    MESSAGE_TYPES.ORCHESTRATOR_RESULT,
    MESSAGE_TYPES.ORCHESTRATOR_ERROR,
  ].includes(messageType);
};

/**
 * Check if a message type represents user input
 */
export const isUserMessage = (messageType) => {
  return [
    MESSAGE_TYPES.USER_MESSAGE,
    MESSAGE_TYPES.ORCHESTRATOR_COMMAND,
  ].includes(messageType);
};

/**
 * Check if a message type has interactive actions
 */
export const hasActions = (messageType) => {
  return MESSAGE_TYPE_DESCRIPTIONS[messageType]?.hasActions || false;
};

/**
 * Get available actions for a message type
 */
export const getAvailableActions = (messageType) => {
  return MESSAGE_TYPE_DESCRIPTIONS[messageType]?.actions || [];
};

/**
 * Check if message type is a status update
 */
export const isStatusUpdate = (messageType) => {
  return messageType === MESSAGE_TYPES.ORCHESTRATOR_STATUS;
};

/**
 * Check if message type is a final result
 */
export const isResult = (messageType) => {
  return messageType === MESSAGE_TYPES.ORCHESTRATOR_RESULT;
};

/**
 * Check if message type is an error
 */
export const isError = (messageType) => {
  return messageType === MESSAGE_TYPES.ORCHESTRATOR_ERROR;
};

// ============================================================================
// EXPORTS
// ============================================================================

const messageTypesExport = {
  MESSAGE_TYPES,
  MESSAGE_TYPE_DESCRIPTIONS,
  MESSAGE_RENDERERS,
  MESSAGE_ROUTER: MessageRouter,
  // Utility functions
  isOrchestratorMessage,
  isUserMessage,
  hasActions,
  getAvailableActions,
  isStatusUpdate,
  isResult,
  isError,
  // Schemas for reference
  USER_MESSAGE_SCHEMA,
  AI_MESSAGE_SCHEMA,
  ORCHESTRATOR_COMMAND_SCHEMA,
  ORCHESTRATOR_STATUS_SCHEMA,
  ORCHESTRATOR_RESULT_SCHEMA,
  ORCHESTRATOR_ERROR_SCHEMA,
};

export default messageTypesExport;
