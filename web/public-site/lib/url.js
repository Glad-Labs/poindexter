/**
 * URL Utilities
 * Handles API URL construction and image URL resolution
 *
 * Validation rules:
 *  - Must be a valid http/https URL
 *  - In production: must be set and must not point to localhost
 *  - In development: falls back to http://localhost:8000 when unset
 */

const IS_PROD = process.env.NODE_ENV === 'production';

function resolveApiBaseUrl() {
  const raw =
    process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_FASTAPI_URL;

  if (!raw) {
    if (IS_PROD) {
      throw new Error(
        '[Config] NEXT_PUBLIC_API_BASE_URL is required in production. ' +
          'Set it in your environment or Vercel/Railway config.'
      );
    }
    return 'http://localhost:8000';
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(
      `[Config] NEXT_PUBLIC_API_BASE_URL="${raw}" is not a valid URL.`
    );
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(
      `[Config] NEXT_PUBLIC_API_BASE_URL="${raw}" must use http or https (got ${parsed.protocol}).`
    );
  }

  const isLocalhost =
    parsed.hostname === 'localhost' ||
    parsed.hostname === '127.0.0.1' ||
    parsed.hostname === '0.0.0.0';

  if (IS_PROD && isLocalhost) {
    throw new Error(
      `[Config] NEXT_PUBLIC_API_BASE_URL="${raw}" points to localhost in production. ` +
        'Set a real backend URL.'
    );
  }

  // Strip trailing slash for consistent path joining
  return raw.replace(/\/$/, '');
}

const FASTAPI_URL = resolveApiBaseUrl();

/**
 * Construct absolute URL for API calls and image assets
 * Handles both relative paths and already-absolute URLs
 * @param {string} path - Relative or absolute path
 * @returns {string} - Absolute URL
 */
export function getAbsoluteURL(path = '') {
  if (!path) return FASTAPI_URL;

  // If already an absolute URL (http:// or https://), return as-is
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }

  // If relative path, prepend base URL
  return `${FASTAPI_URL}${path}`;
}

/**
 * Get the FastAPI base URL
 * @returns {string} - Base API URL
 */
export function getAPIBaseURL() {
  return FASTAPI_URL;
}
