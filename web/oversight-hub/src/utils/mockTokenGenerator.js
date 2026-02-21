/**
 * Mock JWT Token Generator for Development
 *
 * Creates valid JWT tokens that match the backend's expectations.
 * Uses HMAC-SHA256 signing with the same secret as the backend.
 * Format: header.payload.signature (base64-encoded JSON parts)
 *
 * Used for development/testing without a real OAuth provider.
 */

// Development secret - MUST MATCH backend JWT_SECRET environment variable
// This is read from .env.local as JWT_SECRET=development-secret-key-change-in-production
const DEV_JWT_SECRET = 'dev-jwt-secret-change-in-production-to-random-64-chars';

/**
 * Create a mock JWT token that matches backend expectations
 * Generates a properly signed JWT using HMAC-SHA256
 * @param {Object} userData - User data to encode in token
 * @returns {Promise<string>} - Valid JWT token in format header.payload.signature
 */
export const createMockJWTToken = async (userData = {}) => {
  // Token header
  const header = {
    alg: 'HS256',
    typ: 'JWT',
  };

  // Token payload - matches what backend expects
  const now = Math.floor(Date.now() / 1000);
  const expiry = now + 24 * 60 * 60; // 24 hours for development (prevents token expiry during testing)

  const payload = {
    sub: userData.login || 'dev-user',
    user_id: userData.id || 'mock_user_12345',
    email: userData.email || 'dev@example.com',
    username: userData.login || 'dev-user',
    avatar_url:
      userData.avatar_url || 'https://avatars.githubusercontent.com/u/1?v=4',
    name: userData.name || 'Development User',
    auth_provider: 'mock',
    type: 'access', // Required by backend token_validator
    exp: expiry,
    iat: now,
  };

  const headerEncoded = base64UrlEncode(JSON.stringify(header));
  const payloadEncoded = base64UrlEncode(JSON.stringify(payload));

  // Sign with HMAC-SHA256 using the same secret as the backend
  const signatureEncoded = await hmacSha256Sign(
    `${headerEncoded}.${payloadEncoded}`,
    DEV_JWT_SECRET
  );

  return `${headerEncoded}.${payloadEncoded}.${signatureEncoded}`;
};

/**
 * HMAC-SHA256 sign a message using Web Crypto API
 * @param {string} message - Message to sign
 * @param {string} secret - Secret key
 * @returns {Promise<string>} - Base64url encoded signature
 */
async function hmacSha256Sign(message, secret) {
  // Convert secret and message to Uint8Array
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );

  // Sign the message
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    encoder.encode(message)
  );

  // Convert ArrayBuffer to base64url
  // More robust than String.fromCharCode.apply() for larger buffers
  const signatureArray = new Uint8Array(signature);
  let binaryString = '';
  for (let i = 0; i < signatureArray.length; i++) {
    binaryString += String.fromCharCode(signatureArray[i]);
  }
  return base64UrlEncode(binaryString);
}

/**
 * Encode string to base64url format (JWT standard)
 * @param {string} str - String to encode
 * @returns {string} - Base64url encoded string
 */
function base64UrlEncode(str) {
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

/**
 * Decode a JWT token and return the payload
 * @param {string} token - JWT token
 * @returns {Object|null} - Decoded payload or null if invalid
 */
export const decodeJWTToken = (token) => {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) {
      return null;
    }

    const payloadEncoded = parts[1];
    const payloadDecoded = atob(
      payloadEncoded.replace(/-/g, '+').replace(/_/g, '/')
    );

    return JSON.parse(payloadDecoded);
  } catch {
    return null;
  }
};

/**
 * Check if a token is expired
 * @param {string} token - JWT token
 * @returns {boolean} - true if token is expired
 */
export const isTokenExpired = (token) => {
  try {
    const payload = decodeJWTToken(token);
    if (!payload || !payload.exp) {
      return true; // Invalid token is considered expired
    }

    const now = Math.floor(Date.now() / 1000);
    return payload.exp < now;
  } catch {
    return true;
  }
};

/**
 * Get time remaining on token in seconds
 * @param {string} token - JWT token
 * @returns {number} - Seconds remaining, or 0 if expired/invalid
 */
export const getTokenTimeRemaining = (token) => {
  try {
    const payload = decodeJWTToken(token);
    if (!payload || !payload.exp) {
      return 0;
    }

    const now = Math.floor(Date.now() / 1000);
    const remaining = payload.exp - now;
    return Math.max(0, remaining);
  } catch {
    return 0;
  }
};
