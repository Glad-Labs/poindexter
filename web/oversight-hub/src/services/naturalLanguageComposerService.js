import logger from '@/lib/logger';
/**
 * Natural Language Task Composition Service
 *
 * Client-side service for composing capability tasks from natural language requests.
 * Integrates with the backend LLM-powered composition service.
 */

import { getApiUrl } from '../config/apiConfig';

const API_BASE = getApiUrl();

/**
 * Compose a task from natural language request
 *
 * @param {string} request - Natural language request (e.g., "Write a blog post about AI")
 * @param {Object} options - Composition options
 * @param {boolean} options.autoExecute - Whether to auto-execute the task
 * @param {boolean} options.saveTask - Whether to save the task for later use
 * @returns {Promise<Object>} Composition result with suggested task
 */
export async function composeTaskFromNaturalLanguage(request, options = {}) {
  const { autoExecute = false, saveTask = true } = options;

  try {
    const response = await fetch(
      `${API_BASE}/api/tasks/capability/compose-from-natural-language`,
      {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          request,
          auto_execute: autoExecute,
          save_task: saveTask,
        }),
      }
    );

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.detail ||
          errorData.error ||
          `HTTP ${response.status}: ${response.statusText}`
      );
    }

    const data = await response.json();
    return data;
  } catch (error) {
    logger.error('[NLComposer] Composition failed:', error);
    throw new Error(`Failed to compose task: ${error.message}`);
  }
}

/**
 * Compose and immediately execute a task from natural language
 *
 * @param {string} request - Natural language request
 * @param {Object} options - Options (same as composeTaskFromNaturalLanguage)
 * @returns {Promise<Object>} Execution result
 */
export async function composeAndExecuteTask(request, options = {}) {
  try {
    const response = await fetch(
      `${API_BASE}/api/tasks/capability/compose-and-execute`,
      {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          request,
          auto_execute: true,
          save_task: options.saveTask !== false,
        }),
      }
    );

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.detail ||
          errorData.error ||
          `HTTP ${response.status}: ${response.statusText}`
      );
    }

    const data = await response.json();
    return data;
  } catch (error) {
    logger.error('[NLComposer] Execution failed:', error);
    throw new Error(`Failed to execute task: ${error.message}`);
  }
}

/**
 * Parse a request to detect if it's a task composition request
 *
 * Detects patterns like:
 * - "Can you..." (task request)
 * - "Please..." (task request)
 * - "Write...", "Create...", "Generate..." (action verbs)
 * - Uses specific domains: blog, post, content, social, etc.
 *
 * @param {string} message - Message to analyze
 * @returns {Object} { isTaskRequest: boolean, intent: string, confidence: number }
 */
export function detectTaskRequest(message) {
  if (!message || message.trim().length < 10) {
    return { isTaskRequest: false, intent: null, confidence: 0 };
  }

  const lowerMessage = message.toLowerCase();

  // Task request indicators
  const taskKeywords = [
    // Question patterns
    /^(can you|could you|please|would you|can i|could i)\s+/i,
    // Action verbs
    /\b(write|create|generate|compose|write|make|produce|develop|build|analyze|review|check|evaluate|summarize)\b/i,
    // Domain-specific words
    /\b(blog|post|email|newsletter|article|content|social media|report|analysis|document|presentation)\b/i,
    // Command patterns
    /\b(deploy|publish|schedule|automate|execute)\b/i,
  ];

  let matchCount = 0;
  for (const pattern of taskKeywords) {
    if (pattern.test(lowerMessage)) {
      matchCount++;
    }
  }

  const confidence = Math.min(matchCount * 0.3, 1.0);
  const isTaskRequest = confidence >= 0.3;

  return {
    isTaskRequest,
    intent: isTaskRequest ? 'task_composition' : null,
    confidence,
  };
}

/**
 * Format composed task for display in chat
 *
 * @param {Object} compositionResult - Result from composeTaskFromNaturalLanguage
 * @returns {string} Formatted message for chat
 */
export function formatCompositionResult(compositionResult) {
  if (!compositionResult.success) {
    return `❌ Task Composition Failed: ${compositionResult.error || 'Unknown error'}`;
  }

  const { task_definition, explanation, confidence, execution_id } =
    compositionResult;

  if (!task_definition) {
    return `⚠️ ${explanation}`;
  }

  const stepsList = task_definition.steps
    .map((step, idx) => `${idx + 1}. ${step.capability_name}`)
    .join('\n');

  const confidenceEmoji =
    confidence > 0.8 ? '✅' : confidence > 0.6 ? '⚠️' : '❌';

  let result = `${confidenceEmoji} **Task Composed**\n\n`;
  result += `**${task_definition.name}**\n`;
  result += `${task_definition.description}\n\n`;
  result += `**Steps:**\n${stepsList}\n\n`;
  result += `${explanation}`;

  if (execution_id) {
    result += `\n\n✅ Task auto-executed: ${execution_id}`;
  }

  return result;
}

const naturalLanguageComposerService = {
  composeTaskFromNaturalLanguage,
  composeAndExecuteTask,
  detectTaskRequest,
  formatCompositionResult,
};

export default naturalLanguageComposerService;
