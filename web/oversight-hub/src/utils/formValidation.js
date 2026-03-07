import logger from '@/lib/logger';
/**
 * Form Validation Utilities
 *
 * Centralized validation functions for form inputs.
 * Used across all forms in the Oversight Hub to ensure consistent validation rules.
 *
 * Benefits:
 * - Single source of truth for validation rules
 * - Easier to test and maintain
 * - Consistent error messages across the app
 * - Easy to update rules globally
 *
 * @module formValidation
 */

/**
 * Validate email format
 * Accepts both email addresses and usernames
 *
 * @param {string} email - Email or username to validate
 * @returns {boolean} - True if valid email or username format
 *
 * @example
 * validateEmail('user@example.com') // true
 * validateEmail('john_doe') // true
 * validateEmail('invalid@') // false
 */
export const validateEmail = (email) => {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const usernameRegex = /^[a-zA-Z0-9_]+$/;
  return emailRegex.test(email) || usernameRegex.test(email);
};

/**
 * Validate password strength
 * Minimum 6 characters
 *
 * @param {string} password - Password to validate
 * @returns {boolean} - True if password meets minimum requirements
 *
 * @example
 * validatePassword('password123') // true
 * validatePassword('123') // false
 */
export const validatePassword = (password) => {
  return password && password.length >= 6;
};

/**
 * Validate password strength (strict)
 * Requires: At least 8 characters, uppercase, lowercase, number, special char
 *
 * @param {string} password - Password to validate
 * @returns {boolean} - True if password meets strict requirements
 *
 * @example
 * validatePasswordStrict('SecureP@ss1') // true
 * validatePasswordStrict('password123') // false (no uppercase or special char)
 */
export const validatePasswordStrict = (password) => {
  const strictRegex =
    /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$/;
  return strictRegex.test(password);
};

/**
 * Validate TOTP 2FA code
 * Must be exactly 6 digits
 *
 * @param {string} code - 2FA code to validate
 * @returns {boolean} - True if valid TOTP format
 *
 * @example
 * validate2FACode('123456') // true
 * validate2FACode('12345') // false
 * validate2FACode('abcdef') // false
 */
export const validate2FACode = (code) => {
  return /^\d{6}$/.test(code);
};

/**
 * Validate required field (not empty, not whitespace)
 *
 * @param {string} value - Value to validate
 * @returns {boolean} - True if field has content
 *
 * @example
 * validateRequired('text') // true
 * validateRequired('') // false
 * validateRequired('   ') // false
 */
export const validateRequired = (value) => {
  return typeof value === 'string' && value.trim().length > 0;
};

/**
 * Validate username format
 * Alphanumeric and underscores only, 3-20 characters
 *
 * @param {string} username - Username to validate
 * @returns {boolean} - True if valid username format
 *
 * @example
 * validateUsername('john_doe') // true
 * validateUsername('jo') // false (too short)
 * validateUsername('john@doe') // false (special char)
 */
export const validateUsername = (username) => {
  return /^[a-zA-Z0-9_]{3,20}$/.test(username);
};

/**
 * Validate URL format
 *
 * @param {string} url - URL to validate
 * @returns {boolean} - True if valid URL format
 *
 * @example
 * validateURL('https://example.com') // true
 * validateURL('example.com') // false (no protocol)
 */
export const validateURL = (url) => {
  try {
    new URL(url);
    return true;
  } catch {
    return false;
  }
};

/**
 * Validate minimum string length
 *
 * @param {string} value - Value to validate
 * @param {number} minLength - Minimum required length
 * @returns {boolean} - True if value meets minimum length
 *
 * @example
 * validateMinLength('hello', 3) // true
 * validateMinLength('hi', 3) // false
 */
export const validateMinLength = (value, minLength) => {
  return typeof value === 'string' && value.length >= minLength;
};

/**
 * Validate maximum string length
 *
 * @param {string} value - Value to validate
 * @param {number} maxLength - Maximum allowed length
 * @returns {boolean} - True if value does not exceed maximum length
 *
 * @example
 * validateMaxLength('hello', 10) // true
 * validateMaxLength('hello world', 5) // false
 */
export const validateMaxLength = (value, maxLength) => {
  return typeof value === 'string' && value.length <= maxLength;
};

/**
 * Validate number is within range
 *
 * @param {number} value - Value to validate
 * @param {number} min - Minimum value (inclusive)
 * @param {number} max - Maximum value (inclusive)
 * @returns {boolean} - True if value is within range
 *
 * @example
 * validateRange(5, 1, 10) // true
 * validateRange(15, 1, 10) // false
 */
export const validateRange = (value, min, max) => {
  const num = Number(value);
  return !Number.isNaN(num) && num >= min && num <= max;
};

/**
 * Validate email or username
 * Convenience function combining validateEmail
 *
 * @param {string} value - Value to validate
 * @returns {boolean} - True if valid email or username
 *
 * @example
 * validateEmailOrUsername('user@example.com') // true
 * validateEmailOrUsername('john_doe') // true
 */
export const validateEmailOrUsername = (value) => validateEmail(value);

/**
 * Get validation error message for a field
 * Returns appropriate error message based on validation failure
 *
 * @param {string} fieldName - Name of the field for error message
 * @param {string} fieldType - Type of field: 'email', 'password', '2fa', 'username', 'url', 'required'
 * @param {object} options - Additional options (minLength, maxLength, etc)
 * @returns {string} - Error message
 *
 * @example
 * getValidationError('Email', 'email') // 'Please enter a valid email or username'
 * getValidationError('Password', 'password', { minLength: 6 }) // 'Password must be at least 6 characters'
 */
export const getValidationError = (fieldName, fieldType, options = {}) => {
  const messages = {
    email: 'Please enter a valid email or username',
    password: `Password must be at least ${options.minLength || 6} characters`,
    passwordStrict:
      'Password must contain uppercase, lowercase, number and special character (@$!%*?&)',
    '2fa': '2FA code must be 6 digits',
    username:
      'Username must be 3-20 alphanumeric characters (underscore allowed)',
    url: 'Please enter a valid URL (e.g., https://example.com)',
    required: `${fieldName} is required`,
    minLength: `${fieldName} must be at least ${options.minLength} characters`,
    maxLength: `${fieldName} must not exceed ${options.maxLength} characters`,
    range: `${fieldName} must be between ${options.min} and ${options.max}`,
  };

  return messages[fieldType] || 'Invalid input';
};

/**
 * Validate entire login form
 * Validates email/username and password together
 *
 * @param {object} formData - Form data object
 * @param {string} formData.email - Email or username
 * @param {string} formData.password - Password
 * @returns {object} - { isValid: boolean, errors: object }
 *
 * @example
 * const result = validateLoginForm({ email: 'user@example.com', password: 'pass123' });
 * if (result.isValid) {
 *   // Proceed with login
 * } else {
 *   // Show errors
 *   logger.log(result.errors);
 * }
 */
export const validateLoginForm = (formData) => {
  const errors = {};

  if (!validateRequired(formData.email)) {
    errors.email = 'Email or username is required';
  } else if (!validateEmail(formData.email)) {
    errors.email = getValidationError('Email', 'email');
  }

  if (!formData.password) {
    errors.password = 'Password is required';
  } else if (!validatePassword(formData.password)) {
    errors.password = getValidationError('Password', 'password');
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  };
};

/**
 * Validate entire registration form
 * Validates email, password, and username together
 *
 * @param {object} formData - Form data object
 * @param {string} formData.email - Email address
 * @param {string} formData.username - Username
 * @param {string} formData.password - Password
 * @param {string} formData.passwordConfirm - Password confirmation
 * @returns {object} - { isValid: boolean, errors: object }
 *
 * @example
 * const result = validateRegistrationForm({
 *   email: 'user@example.com',
 *   username: 'john_doe',
 *   password: 'SecurePass123',
 *   passwordConfirm: 'SecurePass123'
 * });
 */
export const validateRegistrationForm = (formData) => {
  const errors = {};

  if (!validateRequired(formData.email)) {
    errors.email = 'Email is required';
  } else if (!validateEmail(formData.email)) {
    errors.email = getValidationError('Email', 'email');
  }

  if (!validateRequired(formData.username)) {
    errors.username = 'Username is required';
  } else if (!validateUsername(formData.username)) {
    errors.username = getValidationError('Username', 'username');
  }

  if (!formData.password) {
    errors.password = 'Password is required';
  } else if (!validatePassword(formData.password)) {
    errors.password = getValidationError('Password', 'password');
  }

  if (!formData.passwordConfirm) {
    errors.passwordConfirm = 'Password confirmation is required';
  } else if (formData.password !== formData.passwordConfirm) {
    errors.passwordConfirm = 'Passwords do not match';
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  };
};

const formValidation = {
  validateEmail,
  validatePassword,
  validatePasswordStrict,
  validate2FACode,
  validateRequired,
  validateUsername,
  validateURL,
  validateMinLength,
  validateMaxLength,
  validateRange,
  validateEmailOrUsername,
  getValidationError,
  validateLoginForm,
  validateRegistrationForm,
};

export default formValidation;

// ============================================================================
// Additional utility validators (named exports for direct use)
// ============================================================================

/** Strict email-only validator (does not accept bare usernames) */
export const isValidEmail = (email) => {
  if (!email || typeof email !== 'string') return false;
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return emailRegex.test(email);
};

/** Strong password: >= 8 chars, uppercase, lowercase, number, special char */
export const isStrongPassword = (password) => {
  if (!password || typeof password !== 'string') return false;
  return (
    password.length >= 8 &&
    /[A-Z]/.test(password) &&
    /[a-z]/.test(password) &&
    /[0-9]/.test(password) &&
    /[^A-Za-z0-9]/.test(password)
  );
};

/** Returns a validator that checks string length >= n */
export const minLength = (n) => (value) =>
  typeof value === 'string' && value.length >= n;

/** Returns a validator that checks string length <= n */
export const maxLength = (n) => (value) =>
  typeof value === 'string' && value.length <= n;

/** Only alphanumeric characters (no spaces, underscores, hyphens, etc.) */
export const alphanumericOnly = (str) =>
  typeof str === 'string' && /^[A-Za-z0-9]+$/.test(str);

/** Validates URL with http/https protocol */
export const urlValidation = (url) => {
  if (!url || typeof url !== 'string') return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
};

/** Alias for urlValidation */
export const isValidURL = urlValidation;

/** Validates US phone numbers: 10 digits, optional formatting */
export const phoneValidation = (phone) => {
  if (!phone || typeof phone !== 'string') return false;
  const cleaned = phone.replace(/[\s\-().]/g, '');
  return /^\d{10}$/.test(cleaned);
};

/** Luhn algorithm credit card validator */
export const creditCardValidation = (number) => {
  if (!number || typeof number !== 'string') return false;
  const digits = number.replace(/\D/g, '');
  if (digits.length < 13 || digits.length > 19) return false;
  let sum = 0;
  let shouldDouble = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let digit = parseInt(digits[i], 10);
    if (shouldDouble) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
    shouldDouble = !shouldDouble;
  }
  return sum % 10 === 0;
};

/** Validates date string in YYYY-MM-DD format */
export const isValidDate = (dateStr) => {
  if (!dateStr || typeof dateStr !== 'string') return false;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return false;
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return false;
  // Verify month/day are valid (Date constructor wraps invalid dates)
  const [year, month, day] = dateStr.split('-').map(Number);
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() + 1 === month &&
    date.getUTCDate() === day
  );
};

/** Validates US ZIP code: 5 digits or 5+4 format */
export const validateUSZipCode = (zip) => {
  if (!zip || typeof zip !== 'string') return false;
  return /^\d{5}(-\d{4})?$/.test(zip);
};

/** Analyzes password strength, returns {score: 0-4, feedback: string[]} */
export const passwordStrength = (password) => {
  if (!password || typeof password !== 'string') {
    return { score: 0, feedback: ['Password is required'] };
  }
  const feedback = [];
  let score = 0;
  if (password.length >= 8) score++;
  else feedback.push('Use at least 8 characters');
  if (/[A-Z]/.test(password)) score++;
  else feedback.push('Add uppercase letters');
  if (/[0-9]/.test(password)) score++;
  else feedback.push('Add numbers');
  if (/[^A-Za-z0-9]/.test(password)) score++;
  else feedback.push('Add special characters');
  return { score, feedback };
};

/** Converts text to URL-friendly slug */
export const slugify = (str) => {
  if (!str || typeof str !== 'string') return '';
  return str
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '') // Remove diacritics
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '') // Remove non-alphanumeric (except spaces/hyphens)
    .trim()
    .replace(/[\s-]+/g, '-') // Replace spaces and hyphens with single hyphen
    .replace(/^-+|-+$/g, ''); // Remove leading/trailing hyphens
};

/** Validates task title: 3-255 chars, allows common punctuation */
export const validateTaskTitle = (title) => {
  if (!title || typeof title !== 'string') return false;
  const trimmed = title.trim();
  return trimmed.length >= 3 && trimmed.length <= 255;
};
