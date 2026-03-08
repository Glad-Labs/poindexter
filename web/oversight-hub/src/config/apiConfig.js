import logger from '@/lib/logger';
/**
 * Centralized API Configuration with Strict Validation
 *
 * Security: Validates all API URLs and prevents hardcoded localhost fallbacks
 * Addresses: GitHub issues #47 (strict validation) and #50 (no hardcoded localhost)
 *
 * Usage:
 *   import { getApiUrl, getOllamaUrl } from '@/config/apiConfig';
 *   const apiUrl = getApiUrl(); // throws if invalid
 */

/**
 * Read environment variables in both Vite and legacy CRA styles.
 * Priority:
 * 1) Vite-prefixed keys (VITE_*) via import.meta.env
 * 2) Legacy REACT_APP_* via import.meta.env (in case injected at build time)
 * 3) process.env fallback for older tooling/shims
 */
function getEnv(...keys) {
  const viteEnv =
    typeof import.meta !== 'undefined' && import.meta.env
      ? import.meta.env
      : {};
  const procEnv =
    typeof process !== 'undefined' && process.env ? process.env : {};

  for (const key of keys) {
    if (viteEnv[key] !== undefined && viteEnv[key] !== '') {
      return viteEnv[key];
    }
    if (procEnv[key] !== undefined && procEnv[key] !== '') {
      return procEnv[key];
    }
  }

  return undefined;
}

function getRuntimeMode() {
  return getEnv('NODE_ENV', 'MODE') || 'development';
}

/**
 * Validates that a URL is properly formatted and not localhost in production
 *
 * @param {string} url - URL to validate
 * @param {string} envVarName - Environment variable name (for error messages)
 * @param {boolean} allowLocalhost - Whether localhost is allowed (dev mode only)
 * @throws {Error} If URL is invalid or localhost in production
 * @returns {string} Validated URL
 */
function validateUrl(url, envVarName, allowLocalhost = false) {
  if (!url) {
    throw new Error(
      `[Config Error] ${envVarName} is not set. Please configure it in .env.local`
    );
  }

  // Basic URL format validation
  try {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      throw new Error(`Invalid protocol: ${parsed.protocol}`);
    }
  } catch (error) {
    throw new Error(
      `[Config Error] ${envVarName}="${url}" is not a valid URL: ${error.message}`
    );
  }

  // Prevent localhost in production
  const isProduction = getRuntimeMode() === 'production';
  const isLocalhost =
    url.includes('localhost') ||
    url.includes('127.0.0.1') ||
    url.includes('0.0.0.0');

  if (isProduction && isLocalhost && !allowLocalhost) {
    throw new Error(
      `[Config Error] ${envVarName}="${url}" uses localhost in production. ` +
        `Please set a production URL in your environment configuration.`
    );
  }

  return url;
}

/**
 * Gets the FastAPI backend URL with strict validation
 *
 * Checks multiple environment variables for backward compatibility:
 * - REACT_APP_API_URL (canonical)
 * - REACT_APP_API_BASE_URL (alias)
 * - REACT_APP_AGENT_URL (legacy)
 *
 * @throws {Error} If no valid URL is configured
 * @returns {string} Validated backend API URL
 */
export function getApiUrl() {
  const url =
    getEnv('VITE_API_URL', 'REACT_APP_API_URL') ||
    getEnv('VITE_API_BASE_URL', 'REACT_APP_API_BASE_URL') ||
    getEnv('VITE_AGENT_URL', 'REACT_APP_AGENT_URL');

  try {
    return validateUrl(url, 'REACT_APP_API_URL', false);
  } catch (error) {
    // In development, provide helpful error message
    if (getRuntimeMode() === 'development') {
      logger.error(
        '\n❌ Missing API Configuration!\n\n' +
          'To fix this:\n' +
          '1. Copy .env.example to .env.local\n' +
          '2. Set REACT_APP_API_URL=http://localhost:8000\n' +
          '3. Restart the dev server\n'
      );
    }
    throw error;
  }
}

/**
 * Gets the Ollama local model server URL with strict validation
 *
 * @param {boolean} required - Whether to throw error if not configured (default: false)
 * @throws {Error} If required=true and Ollama is not configured
 * @returns {string|null} Validated Ollama URL or null if not configured
 */
export function getOllamaUrl(required = false) {
  const url =
    getEnv('VITE_OLLAMA_URL', 'REACT_APP_OLLAMA_URL') ||
    getEnv('VITE_OLLAMA_BASE_URL', 'REACT_APP_OLLAMA_BASE_URL');

  // If not configured and not required, return null
  if (!url && !required) {
    return null;
  }

  try {
    // Ollama is always localhost in development (local AI)
    return validateUrl(
      url || 'http://localhost:11434',
      'REACT_APP_OLLAMA_URL',
      true
    );
  } catch (error) {
    if (required) {
      logger.error(
        '\n❌ Ollama Configuration Error!\n\n' +
          'To use local Ollama models:\n' +
          '1. Install Ollama: https://ollama.ai\n' +
          '2. Start Ollama: ollama serve\n' +
          '3. Optionally set REACT_APP_OLLAMA_URL=http://localhost:11434 in .env.local\n'
      );
      throw error;
    }
    return null;
  }
}

/**
 * Gets the WebSocket base URL (derived from API URL)
 *
 * @returns {string} WebSocket URL (ws:// or wss://)
 */
export function getWebSocketUrl() {
  const apiUrl = getApiUrl();
  return apiUrl.replace(/^http/, 'ws');
}

/**
 * Gets the public site URL for link generation
 *
 * @returns {string} Validated public site URL
 */
export function getPublicSiteUrl() {
  const url =
    getEnv('VITE_PUBLIC_SITE_URL', 'REACT_APP_PUBLIC_SITE_URL') ||
    getEnv('VITE_SITE_URL', 'REACT_APP_SITE_URL');

  try {
    return validateUrl(url, 'REACT_APP_PUBLIC_SITE_URL', false);
  } catch (error) {
    // Fallback to localhost in development only
    if (getRuntimeMode() === 'development') {
      logger.warn(
        '⚠️  REACT_APP_PUBLIC_SITE_URL not set, using localhost:3000'
      );
      return 'http://localhost:3000';
    }
    throw error;
  }
}

/**
 * Configuration object with all validated URLs
 *
 * Use this when you need multiple URLs at once.
 * Each property throws on access if not properly configured.
 */
export const config = {
  get api() {
    return getApiUrl();
  },
  get ws() {
    return getWebSocketUrl();
  },
  get ollama() {
    return getOllamaUrl(false);
  },
  get publicSite() {
    return getPublicSiteUrl();
  },
  get isProduction() {
    return getRuntimeMode() === 'production';
  },
  get isDevelopment() {
    return getRuntimeMode() === 'development';
  },
};

export default config;
