import logger from '@/lib/logger';
/**
 * useFormValidation Hook
 *
 * Custom React hook for managing form state and validation.
 * Provides a clean interface for form handling with built-in validation.
 *
 * Features:
 * - Form state management
 * - Field-level validation
 * - Form-level validation
 * - Error handling and display
 * - Form reset functionality
 * - Dirty state tracking
 *
 * @module hooks/useFormValidation
 */

import { useState, useCallback } from 'react';
import {
  validateLoginForm,
  validateRegistrationForm,
} from '../utils/formValidation';

/**
 * useFormValidation Hook
 *
 * Manages form state and validation with minimal boilerplate.
 * Provides methods for handling field changes, form submission, and reset.
 *
 * @param {object} options - Configuration options
 * @param {object} options.initialValues - Initial form values
 * @param {function} options.onSubmit - Callback when form is submitted (after validation)
 * @param {object} options.validators - Custom validators for specific fields
 * @param {string} options.formType - Pre-defined form type: 'login', 'registration', or 'custom'
 *
 * @returns {object} - Form state and methods
 *   @returns {object} .values - Current form values
 *   @returns {object} .errors - Current form errors
 *   @returns {object} .touched - Fields that have been touched
 *   @returns {boolean} .isSubmitting - Whether form is being submitted
 *   @returns {boolean} .isDirty - Whether any field has been changed
 *   @returns {function} .handleChange - Handle field change events
 *   @returns {function} .handleBlur - Handle field blur events
 *   @returns {function} .handleSubmit - Handle form submission
 *   @returns {function} .setFieldValue - Programmatically set field value
 *   @returns {function} .setFieldError - Programmatically set field error
 *   @returns {function} .reset - Reset form to initial values
 *   @returns {function} .setValues - Set all form values
 *   @returns {function} .setErrors - Set all form errors
 *   @returns {function} .getFieldProps - Get props for an input field
 *
 * @example
 * // Login form
 * const form = useFormValidation({
 *   initialValues: { email: '', password: '' },
 *   formType: 'login',
 *   onSubmit: async (values) => {
 *     await api.login(values);
 *   }
 * });
 *
 * // In component:
 * <TextField {...form.getFieldProps('email')} />
 * <TextField {...form.getFieldProps('password')} type="password" />
 * <button onClick={form.handleSubmit}>Login</button>
 */
export const useFormValidation = ({
  initialValues = {},
  onSubmit = null,
  validators = {},
  formType = 'custom',
} = {}) => {
  const [values, setValues] = useState(initialValues);
  const [errors, setErrors] = useState({});
  const [touched, setTouched] = useState({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Track if form has been modified
  const isDirty = Object.keys(initialValues).some(
    (key) => values[key] !== initialValues[key]
  );

  /**
   * Validate specific field
   */
  const validateField = useCallback(
    (fieldName, value) => {
      // Use custom validator if provided
      if (validators[fieldName]) {
        return validators[fieldName](value);
      }

      // No validation for this field
      return null;
    },
    [validators]
  );

  /**
   * Validate entire form
   */
  const validateForm = useCallback(() => {
    let formErrors = {};

    // Use pre-defined validators for known form types
    if (formType === 'login') {
      const result = validateLoginForm(values);
      formErrors = result.errors;
    } else if (formType === 'registration') {
      const result = validateRegistrationForm(values);
      formErrors = result.errors;
    } else {
      // Custom validation for each field
      Object.keys(values).forEach((fieldName) => {
        const error = validateField(fieldName, values[fieldName]);
        if (error) {
          formErrors[fieldName] = error;
        }
      });
    }

    setErrors(formErrors);
    return Object.keys(formErrors).length === 0;
  }, [values, formType, validateField]);

  /**
   * Handle field change
   */
  const handleChange = useCallback(
    (e) => {
      const { name, value, type, checked } = e.target;
      const fieldValue = type === 'checkbox' ? checked : value;

      setValues((prev) => ({
        ...prev,
        [name]: fieldValue,
      }));

      // Clear error when user starts typing
      if (errors[name]) {
        setErrors((prev) => ({
          ...prev,
          [name]: null,
        }));
      }
    },
    [errors]
  );

  /**
   * Handle field blur (mark as touched)
   */
  const handleBlur = useCallback(
    (e) => {
      const { name } = e.target;

      setTouched((prev) => ({
        ...prev,
        [name]: true,
      }));

      // Validate on blur
      const error = validateField(name, values[name]);
      if (error) {
        setErrors((prev) => ({
          ...prev,
          [name]: error,
        }));
      }
    },
    [values, validateField]
  );

  /**
   * Handle form submission
   */
  const handleSubmit = useCallback(
    async (e) => {
      if (e && e.preventDefault) {
        e.preventDefault();
      }

      // Mark all fields as touched
      const touchedFields = {};
      Object.keys(values).forEach((key) => {
        touchedFields[key] = true;
      });
      setTouched(touchedFields);

      // Validate form
      const isValid = validateForm();

      if (!isValid) {
        return;
      }

      // Call onSubmit callback
      if (onSubmit) {
        try {
          setIsSubmitting(true);
          await onSubmit(values);
        } catch (error) {
          logger.error('Form submission error:', error);
          setErrors((prev) => ({
            ...prev,
            _global: error.message || 'An error occurred',
          }));
        } finally {
          setIsSubmitting(false);
        }
      }
    },
    [values, validateForm, onSubmit]
  );

  /**
   * Programmatically set field value
   */
  const setFieldValue = useCallback((fieldName, value) => {
    setValues((prev) => ({
      ...prev,
      [fieldName]: value,
    }));
  }, []);

  /**
   * Programmatically set field error
   */
  const setFieldError = useCallback((fieldName, error) => {
    setErrors((prev) => ({
      ...prev,
      [fieldName]: error,
    }));
  }, []);

  /**
   * Set a general (non-field-specific) error
   */
  const setGeneralError = useCallback((message) => {
    setErrors((prev) => ({ ...prev, general: message }));
  }, []);

  /**
   * Clear all form errors
   */
  const clearErrors = useCallback(() => {
    setErrors({});
  }, []);

  /**
   * Clear a specific field error
   */
  const clearFieldError = useCallback((fieldName) => {
    setErrors((prev) => {
      const next = { ...prev };
      delete next[fieldName];
      return next;
    });
  }, []);

  /**
   * Reset form to initial values (or custom values if provided)
   */
  const reset = useCallback(
    (customValues) => {
      setValues(customValues || initialValues);
      setErrors({});
      setTouched({});
      setIsSubmitting(false);
    },
    [initialValues]
  );

  /**
   * Set all form values
   */
  const setAllValues = useCallback((newValues) => {
    setValues(newValues);
  }, []);

  /**
   * Set all form errors
   */
  const setAllErrors = useCallback((newErrors) => {
    setErrors(newErrors);
  }, []);

  /**
   * Get props for an input field (helper for quick integration)
   * Returns name, value, onChange, onBlur, and error flag
   */
  const getFieldProps = useCallback(
    (fieldName) => {
      return {
        name: fieldName,
        value: values[fieldName] !== undefined ? values[fieldName] : '',
        onChange: (e) => {
          // Inject fieldName; preserve checkbox type so handleChange detects it
          const target = { name: fieldName, ...e.target };
          if (typeof e.target.checked === 'boolean' && !target.type) {
            target.type = 'checkbox';
          }
          handleChange({ ...e, target });
        },
        onBlur: (e) => {
          // Works with or without an event argument
          const event = e && e.target ? e : { target: { name: fieldName } };
          handleBlur({
            ...event,
            target: { name: fieldName, ...event.target },
          });
        },
        error: touched[fieldName] && !!errors[fieldName],
        helperText: touched[fieldName] && errors[fieldName],
      };
    },
    [values, touched, errors, handleChange, handleBlur]
  );

  return {
    // State
    values,
    errors,
    touched,
    isSubmitting,
    isDirty,

    // Methods
    handleChange,
    handleBlur,
    handleSubmit,
    setFieldValue,
    setFieldError,
    setGeneralError,
    clearErrors,
    clearFieldError,
    reset,
    setValues: setAllValues,
    setErrors: setAllErrors,
    getFieldProps,
    validateForm,
    validateField,
  };
};

export default useFormValidation;
