/**
 * Ollama Service - Specialized functions for Ollama local model interactions
 *
 * This service handles communication with the Ollama API endpoint.
 * Falls back gracefully if Ollama is not available.
 *
 * Uses API proxy at /api/ollama/* to maintain security and centralized authentication.
 */

import { getApiUrl } from '../config/apiConfig';

const API_BASE_URL = getApiUrl();
const OLLAMA_TIMEOUT = 10000; // 10 second timeout for Ollama operations

/**
 * Get Ollama endpoint - through API proxy for security
 * @returns {string} Base URL for Ollama API proxy
 */
function getOllamaEndpoint() {
  return `${API_BASE_URL}/api/ollama`;
}

/**
 * Get list of available Ollama models (pulls)
 * @returns {Promise<Array>} Array of model objects or empty array if unavailable
 */
export async function getOllamaModels() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), OLLAMA_TIMEOUT);

    const response = await fetch(`${getOllamaEndpoint()}/tags`, {
      method: 'GET',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      return [];
    }

    const data = await response.json();
    return data.models || [];
  } catch {
    return [];
  }
}

/**
 * Check if Ollama is running and accessible
 * @returns {Promise<boolean>} True if Ollama is available
 */
export async function isOllamaAvailable() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    const response = await fetch(`${getOllamaEndpoint()}/health`, {
      method: 'GET',
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      return false;
    }

    const data = await response.json();
    return data.connected === true;
  } catch {
    return false;
  }
}

/**
 * Test a model by generating text with it
 * @param {string} modelId - Model identifier (e.g., "mistral:latest")
 * @param {string} prompt - Prompt to send to the model
 * @param {object} options - Additional options (temperature, top_p, etc.)
 * @returns {Promise<string>} Generated text from the model
 */
export async function generateWithOllamaModel(modelId, prompt, options = {}) {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000); // 1 minute for generation

    const response = await fetch(`${getOllamaEndpoint()}/generate`, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: modelId,
        prompt,
        stream: false,
        temperature: options.temperature || 0.7,
        top_p: options.top_p || 0.9,
        ...options,
      }),
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      throw new Error(`Generation failed with status ${response.status}`);
    }

    const data = await response.json();
    return data.response || '';
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('Generation timed out - model may be stuck');
    }
    throw error;
  }
}

/**
 * Stream generation from Ollama model (real-time response)
 * @param {string} modelId - Model identifier
 * @param {string} prompt - Prompt to send
 * @param {function} onChunk - Callback for each streamed chunk
 * @param {object} options - Additional options
 * @returns {Promise<string>} Full generated text
 */
export async function streamOllamaGeneration(
  modelId,
  prompt,
  onChunk = null,
  options = {}
) {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 120000); // 2 minutes for streaming

    const response = await fetch(`${getOllamaEndpoint()}/generate`, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: modelId,
        prompt,
        stream: true,
        temperature: options.temperature || 0.7,
        ...options,
      }),
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      throw new Error(
        `Stream generation failed with status ${response.status}`
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n').filter((line) => line.trim());

      for (const line of lines) {
        try {
          const json = JSON.parse(line);
          if (json.response) {
            fullText += json.response;
            if (onChunk) {
              onChunk(json.response);
            }
          }
        } catch {
          // Skip invalid JSON lines
        }
      }
    }

    return fullText;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('Stream generation timed out');
    }
    throw error;
  }
}

/**
 * Get model details and capabilities
 * @param {string} modelId - Model identifier
 * @returns {Promise<object>} Model information
 */
export async function getOllamaModelInfo(modelId) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 5000);

  const response = await fetch(`${getOllamaEndpoint()}/show`, {
    method: 'POST',
    signal: controller.signal,
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      name: modelId,
    }),
  });

  clearTimeout(timeoutId);

  if (!response.ok) {
    throw new Error(`Could not fetch model info: ${response.status}`);
  }

  return await response.json();
}
