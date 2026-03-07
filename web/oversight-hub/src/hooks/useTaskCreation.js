import logger from '@/lib/logger';
/**
 * useTaskCreation.js
 *
 * Custom hook for managing task creation workflow
 * Handles:
 * - Form submission and validation
 * - API calls (image generation, blog post creation, etc.)
 * - Error handling and loading states
 * - Model selection and cost estimation
 *
 * Usage:
 *   const { submit, isSubmitting, error } = useTaskCreation(onSuccess);
 *   submit({ taskType: 'blog_post', formData: {...}, models: {...} });
 */

import { useState } from 'react';
import { makeRequest } from '../services/cofounderAgentClient';

const useTaskCreation = (onSuccess) => {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  /**
   * Submit a new task to the orchestrator
   * Handles different task types and their specific requirements
   */
  const submit = async ({ taskType, formData, modelSelection }) => {
    try {
      setIsSubmitting(true);
      setError(null);

      // Validate task type
      if (!taskType) {
        throw new Error('Please select a task type');
      }

      // Validate form data (basic)
      if (Object.keys(formData).length === 0) {
        throw new Error('Please fill in the required fields');
      }

      // Prepare payload based on task type
      const payload = {
        task_type: taskType,
        input_data: formData,
        model_selections: modelSelection?.modelSelections || {},
        quality_preference: modelSelection?.qualityPreference || 'balanced',
      };

      // Special handling for image generation
      if (taskType === 'image_generation') {
        const imageResult = await makeRequest(
          '/api/media/generate-image',
          'POST',
          {
            prompt: formData.description,
            count: formData.count || 1,
            style: formData.style || 'realistic',
          },
          false,
          null,
          120000 // 2 min timeout for image generation
        );

        if (!imageResult.success) {
          throw new Error(imageResult.error || 'Image generation failed');
        }

        payload.image_urls = imageResult.images;
      }

      // Submit task to orchestrator
      const result = await makeRequest(
        '/api/tasks/create',
        'POST',
        payload,
        false,
        null,
        30000 // 30 second timeout
      );

      if (!result.success) {
        throw new Error(result.error || 'Failed to create task');
      }

      // Call success callback with task result
      if (onSuccess) {
        onSuccess(result);
      }

      return result;
    } catch (err) {
      const errorMessage =
        err.message || 'An error occurred while creating the task';
      setError(errorMessage);
      logger.error('Task creation error:', err);
      throw err;
    } finally {
      setIsSubmitting(false);
    }
  };

  /**
   * Clear error state
   */
  const clearError = () => {
    setError(null);
  };

  return {
    submit,
    isSubmitting,
    error,
    clearError,
  };
};

export default useTaskCreation;
