import logger from '@/lib/logger';
import React, { useState, useCallback } from 'react';
import { createTask, makeRequest } from '../../services/cofounderAgentClient';
import ModelSelectionPanel from '../ModelSelectionPanel';
import { WritingStyleSelector } from '../WritingStyleSelector';

const CreateTaskModal = ({ isOpen, onClose, onTaskCreated }) => {
  const [taskType, setTaskType] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [formData, setFormData] = useState({});
  const [selectedWritingStyleId, setSelectedWritingStyleId] = useState(null);
  const [modelSelection, setModelSelection] = useState({
    modelSelections: {
      research: 'auto',
      outline: 'auto',
      draft: 'auto',
      assess: 'auto',
      refine: 'auto',
      finalize: 'auto',
    },
    qualityPreference: 'balanced',
    estimatedCost: 0.015,
  });

  // Memoize the model selection callback to prevent infinite re-renders
  const handleModelSelectionChange = useCallback((selection) => {
    setModelSelection(selection);
  }, []);

  // Task type definitions with required fields
  const taskTypes = {
    blog_post: {
      label: '📝 Blog Post',
      description: 'Create a comprehensive blog article',
      fields: [
        { name: 'topic', label: 'Topic', type: 'text', required: true },
        {
          name: 'word_count',
          label: 'Target Word Count',
          type: 'number',
          required: true,
          defaultValue: 1500,
          min: 300,
          max: 5000,
          description: 'Target word count for the article (300-5000 words)',
        },
        {
          name: 'style',
          label: 'Writing Style',
          type: 'select',
          required: true,
          options: [
            'technical',
            'narrative',
            'listicle',
            'educational',
            'thought-leadership',
          ],
          description: 'Choose the structure for your content',
        },
        {
          name: 'tone',
          label: 'Tone',
          type: 'select',
          required: true,
          options: [
            'professional',
            'casual',
            'academic',
            'inspirational',
            'authoritative',
            'friendly',
          ],
          description: 'Choose the voice/tone for your content',
        },
        {
          name: 'word_count_tolerance',
          label: 'Word Count Tolerance',
          type: 'range',
          required: false,
          defaultValue: 10,
          min: 5,
          max: 20,
          step: 1,
          description: 'Acceptable variance from target: ±5-20%',
        },
        {
          name: 'strict_mode',
          label: 'Enforce Constraints',
          type: 'checkbox',
          required: false,
          defaultValue: false,
          description: 'If enabled, task fails if constraints are violated',
        },
      ],
    },
    image_generation: {
      label: '🖼️ Image Generation',
      description: 'Generate custom images',
      fields: [
        {
          name: 'description',
          label: 'Image Description',
          type: 'textarea',
          required: true,
        },
        {
          name: 'count',
          label: 'Number of Images',
          type: 'number',
          required: true,
          defaultValue: 1,
          min: 1,
          max: 5,
        },
        {
          name: 'style',
          label: 'Style',
          type: 'select',
          required: true,
          options: ['realistic', 'abstract', 'illustration', 'cartoon'],
        },
        {
          name: 'resolution',
          label: 'Resolution',
          type: 'select',
          required: true,
          options: ['512x512', '768x768', '1024x1024'],
          defaultValue: '768x768',
        },
        {
          name: 'imageSource',
          label: 'Image Source',
          type: 'select',
          required: true,
          options: ['pexels', 'sdxl', 'both'],
          defaultValue: 'pexels',
          description:
            'Choose "pexels" to search free stock photos (fast), "sdxl" for AI generation (slower), or "both" to try Pexels first then fall back to SDXL',
        },
      ],
    },
    social_media_post: {
      label: '📱 Social Media Post',
      description: 'Create a social media post',
      fields: [
        {
          name: 'platform',
          label: 'Platform',
          type: 'select',
          required: true,
          options: ['twitter', 'facebook', 'instagram', 'linkedin', 'tiktok'],
        },
        { name: 'topic', label: 'Topic', type: 'text', required: true },
        {
          name: 'tone',
          label: 'Tone',
          type: 'select',
          required: true,
          options: ['professional', 'casual', 'academic', 'inspirational'],
        },
        {
          name: 'include_hashtags',
          label: 'Include Hashtags',
          type: 'select',
          required: true,
          options: ['yes', 'no'],
          defaultValue: 'yes',
        },
      ],
    },
    email_campaign: {
      label: '📧 Email Campaign',
      description: 'Create an email campaign',
      fields: [
        {
          name: 'subject',
          label: 'Email Subject',
          type: 'text',
          required: true,
        },
        { name: 'goal', label: 'Campaign Goal', type: 'text', required: true },
        {
          name: 'audience',
          label: 'Target Audience',
          type: 'text',
          required: false,
        },
        {
          name: 'tone',
          label: 'Tone',
          type: 'select',
          required: true,
          options: ['professional', 'casual', 'academic', 'inspirational'],
        },
      ],
    },
    content_brief: {
      label: '📋 Content Brief',
      description: 'Create a content strategy brief',
      fields: [
        { name: 'topic', label: 'Content Topic', type: 'text', required: true },
        {
          name: 'audience',
          label: 'Target Audience',
          type: 'text',
          required: true,
        },
        {
          name: 'goals',
          label: 'Content Goals',
          type: 'textarea',
          required: false,
        },
        {
          name: 'platforms',
          label: 'Target Platforms (comma-separated)',
          type: 'text',
          required: false,
        },
      ],
    },
  };

  const handleTaskTypeSelect = (type) => {
    setTaskType(type);

    // Initialize form with default values for fields
    const defaultData = {};
    const fields = taskTypes[type]?.fields || [];
    fields.forEach((field) => {
      if (field.defaultValue !== undefined) {
        defaultData[field.name] = field.defaultValue;
      }
    });
    
    logger.log('📝 [CreateTaskModal] Task type selected:', type);
    logger.log('📝 [CreateTaskModal] Default data initialized:', defaultData);
    
    setFormData(defaultData);
    setSelectedWritingStyleId(null); // Reset writing style when changing task type
    setError(null);
  };

  const handleInputChange = (fieldName, value) => {
    setFormData({
      ...formData,
      [fieldName]: value,
    });
  };

  const handleWritingStyleChange = (styleId) => {
    setSelectedWritingStyleId(styleId);
  };

  const validateForm = () => {
    if (!taskType) {
      setError('Please select a task type');
      return false;
    }

    const fields = taskTypes[taskType].fields;
    for (const field of fields) {
      if (field.required && !formData[field.name]) {
        setError(`${field.label} is required`);
        return false;
      }
    }

    // Validate that we have at least a topic for the backend
    const hasTopic = formData.topic || formData.description || formData.title;
    if (!hasTopic) {
      setError('Please provide a topic or description');
      return false;
    }

    return true;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!validateForm()) return;

    setSubmitting(true);
    setError(null);

    try {
      // ✅ ROUTE TO CORRECT ENDPOINT BASED ON TASK TYPE
      let taskPayload;

      if (taskType === 'image_generation') {
        // 🖼️ Handle image generation task
        logger.log('🖼️ Generating images with:', formData);

        // Determine which image sources to try based on user selection
        const usePexels =
          formData.imageSource === 'pexels' || formData.imageSource === 'both';
        const useSDXL =
          formData.imageSource === 'sdxl' || formData.imageSource === 'both';

        // ✅ Use API client with environment variable support
        const imagePayload = {
          prompt: formData.description,
          title: formData.description.substring(0, 50), // Use first 50 chars as title
          use_pexels: usePexels,
          use_generation: useSDXL,
          count: formData.count || 1,
          style: formData.style || 'realistic',
          resolution: formData.resolution || '1024x1024',
        };

        // ✅ Call through makeRequest with proper auth and config
        const imageResult = await makeRequest(
          '/api/media/generate-image',
          'POST',
          imagePayload,
          false,
          null,
          120000 // 2 min timeout for image generation
        );

        if (!imageResult) {
          throw new Error('Image generation failed: No response from server');
        }

        if (!imageResult) {
          throw new Error('Image generation failed: No response from server');
        }

        if (!imageResult.success) {
          throw new Error(imageResult.message || 'Image generation failed');
        }

        logger.log('✅ Image generated:', imageResult);

        // Create task record with image results
        taskPayload = {
          task_type: 'image_generation', // Correct type for image generation tasks
          task_name: `Image: ${formData.description.substring(0, 40)}...`,
          topic: formData.description || '',
          category: 'image_generation',
          metadata: {
            task_type: 'image_generation',
            style: formData.style || 'realistic',
            resolution: formData.resolution || '1024x1024',
            count: formData.count || 1,
            image_url: imageResult.image_url,
            image_source: imageResult.image?.source || 'generated',
            generation_time: imageResult.generation_time,
            image_metadata: imageResult.image,
            status: 'completed',
            result: {
              success: true,
              image_url: imageResult.image_url,
              generation_time: imageResult.generation_time,
            },
          },
        };
      } else if (taskType === 'blog_post') {
        // Create blog post task using unified endpoint
        const context = selectedWritingStyleId
          ? { writing_style_id: selectedWritingStyleId }
          : undefined;

        // 🔍 CRITICAL: Don't use fallback defaults - let Pydantic schema handle defaults
        // If user didn't select style/tone, form validation should have caught it
        logger.log('📤 [CreateTaskModal] Form data before payload:', formData);
        logger.log('📤 [CreateTaskModal] Model selections:', modelSelection);

        taskPayload = {
          task_type: 'blog_post',
          task_name: `Blog: ${formData.topic}`,
          topic: formData.topic || '',
          category: 'blog_post',
          // ⚠️ IMPORTANT: Send actual values or undefined, not hardcoded fallbacks
          // This allows backend Pydantic defaults to apply correctly
          style: formData.style || undefined,
          tone: formData.tone || undefined,
          target_length: parseInt(formData.word_count) || 1500,
          generate_featured_image: formData.generate_featured_image !== false,
          tags: formData.keywords
            ? formData.keywords
                .split(',')
                .map((k) => k.trim())
                .filter((k) => k)
            : [],
          models_by_phase: modelSelection.modelSelections || {},
          quality_preference: modelSelection.qualityPreference || 'balanced',
          context,
          metadata: {
            task_type: 'blog_post',
            task_name: `Blog: ${formData.topic}`,
            style: formData.style,
            tone: formData.tone,
            word_count: parseInt(formData.word_count) || 1500,
            generate_featured_image: formData.generate_featured_image !== false,
            publish_mode: 'draft',
            writing_style_id: selectedWritingStyleId,
          },
        };

        logger.log('📤 [CreateTaskModal] Final payload:', taskPayload);
      } else {
        // Use generic task endpoint for other types
        // Generic task payload - also remove hardcoded fallbacks
        logger.log('📤 [CreateTaskModal] Generic task - Form data:', formData);

        taskPayload = {
          task_type: taskType || 'blog_post',
          topic: formData.topic || formData.description || '',
          category: formData.category || taskType || 'general',
          // Use undefined instead of hardcoded fallbacks
          style: formData.style || undefined,
          tone: formData.tone || undefined,
          models_by_phase: modelSelection.modelSelections || {},
          quality_preference: modelSelection.qualityPreference || 'balanced',
          metadata: {
            task_type: taskType,
            style: formData.style,
            tone: formData.tone,
            word_count: formData.word_count,
            ...formData, // Include all original form data in metadata
          },
        };

        logger.log('📤 [CreateTaskModal] Generic task payload:', taskPayload);
      }

      logger.log('📤 Creating task:', taskPayload);

      // ✅ Use API client instead of hardcoded fetch
      const result = await createTask(taskPayload);
      logger.log('✅ Task created successfully:', result);

      // ✅ Validate response has required fields
      if (!result || !result.id) {
        throw new Error(
          'Invalid response: task was created but no ID returned'
        );
      }

      // Notify parent with the new task data and reset
      if (onTaskCreated) {
        onTaskCreated(result);
      }
      setTaskType('');
      setFormData({});
    } catch (err) {
      // Properly extract error message from various error object formats
      let errorMessage = 'Unknown error';

      if (typeof err === 'string') {
        errorMessage = err;
      } else if (err instanceof Error) {
        errorMessage = err.message;
      } else if (err && typeof err === 'object') {
        // Handle API error responses with detail/message fields
        if (err.response?.detail) {
          errorMessage =
            typeof err.response.detail === 'string'
              ? err.response.detail
              : JSON.stringify(err.response.detail);
        } else if (err.response?.message) {
          errorMessage =
            typeof err.response.message === 'string'
              ? err.response.message
              : JSON.stringify(err.response.message);
        } else if (err.message) {
          errorMessage = err.message;
        }
      }

      setError(`Failed to create task: ${errorMessage}`);
      logger.error('Task creation error:', {
        message: errorMessage,
        status: err?.status,
        response: err?.response,
        fullError: err,
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  const currentTaskType = taskTypes[taskType];
  const currentFields = currentTaskType ? currentTaskType.fields : [];

  return (
    <div className="fixed inset-0 bg-black bg-opacity-70 flex justify-center items-center z-50 p-4">
      <div className="bg-gray-800 rounded-lg shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-gray-900 border-b border-cyan-500 p-6 flex justify-between items-center">
          <h2 className="text-2xl font-bold text-cyan-400">
            {taskType ? currentTaskType.label : '🚀 Create New Task'}
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-2xl"
            disabled={submitting}
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="p-6">
          {error && (
            <div className="mb-4 p-4 bg-red-900 border border-red-500 rounded-md text-red-100">
              ⚠️ {error}
            </div>
          )}

          {/* Task Type Selection */}
          {!taskType ? (
            <div>
              <h3 className="text-lg font-semibold text-gray-300 mb-4">
                Select Task Type
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {Object.entries(taskTypes).map(([key, typeConfig]) => (
                  <button
                    key={key}
                    onClick={() => handleTaskTypeSelect(key)}
                    className="p-4 border-2 border-gray-600 rounded-lg hover:border-cyan-500 hover:bg-gray-700 transition text-left group"
                  >
                    <div className="text-xl font-bold text-cyan-400 group-hover:text-cyan-300">
                      {typeConfig.label}
                    </div>
                    <div className="text-sm text-gray-400 mt-1">
                      {typeConfig.description}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit}>
              {/* Back Button & Title */}
              <button
                type="button"
                onClick={() => {
                  setTaskType('');
                  setFormData({});
                }}
                className="text-cyan-400 hover:text-cyan-300 text-sm mb-4 flex items-center gap-2"
              >
                ← Back to Task Types
              </button>

              {/* Dynamic Form Fields */}
              <div className="space-y-4">
                {currentFields.map((field) => (
                  <div key={field.name}>
                    <label
                      htmlFor={field.name}
                      className="block text-gray-300 mb-2 font-medium"
                    >
                      {field.label}
                      {field.required && (
                        <span className="text-red-400 ml-1">*</span>
                      )}
                    </label>
                    {field.description && field.type !== 'checkbox' && (
                      <p className="text-xs text-gray-400 mb-2">
                        {field.description}
                      </p>
                    )}

                    {field.type === 'text' || field.type === 'number' ? (
                      <input
                        id={field.name}
                        type={field.type}
                        value={
                          formData[field.name] ||
                          (field.defaultValue ? field.defaultValue : '')
                        }
                        onChange={(e) =>
                          handleInputChange(field.name, e.target.value)
                        }
                        placeholder={field.label}
                        min={field.min}
                        max={field.max}
                        className="w-full p-3 bg-gray-700 text-white rounded-md border border-gray-600 focus:outline-none focus:ring-2 focus:ring-cyan-500 placeholder-gray-500"
                      />
                    ) : field.type === 'range' ? (
                      <div className="flex items-center gap-4">
                        <input
                          id={field.name}
                          type="range"
                          value={
                            formData[field.name] ||
                            (field.defaultValue
                              ? field.defaultValue
                              : field.min || 0)
                          }
                          onChange={(e) =>
                            handleInputChange(field.name, e.target.value)
                          }
                          min={field.min || 5}
                          max={field.max || 20}
                          step={field.step || 1}
                          className="flex-1 h-2 bg-gray-600 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                        />
                        <span className="text-gray-300 font-medium min-w-[3rem] text-right">
                          {formData[field.name] ||
                            field.defaultValue ||
                            field.min ||
                            0}
                          %
                        </span>
                      </div>
                    ) : field.type === 'checkbox' ? (
                      <div className="flex items-center gap-3">
                        <input
                          id={field.name}
                          type="checkbox"
                          checked={
                            formData[field.name] === true ||
                            formData[field.name] === 'true'
                          }
                          onChange={(e) =>
                            handleInputChange(field.name, e.target.checked)
                          }
                          className="w-5 h-5 bg-gray-700 border border-gray-600 rounded cursor-pointer accent-cyan-500"
                        />
                        <label
                          htmlFor={field.name}
                          className="text-gray-300 cursor-pointer"
                        >
                          {field.description || 'Enable this option'}
                        </label>
                      </div>
                    ) : field.type === 'textarea' ? (
                      <textarea
                        id={field.name}
                        value={formData[field.name] || ''}
                        onChange={(e) =>
                          handleInputChange(field.name, e.target.value)
                        }
                        placeholder={field.label}
                        rows={4}
                        className="w-full p-3 bg-gray-700 text-white rounded-md border border-gray-600 focus:outline-none focus:ring-2 focus:ring-cyan-500 placeholder-gray-500 resize-none"
                      />
                    ) : field.type === 'select' ? (
                      <select
                        id={field.name}
                        value={
                          formData[field.name] ||
                          (field.defaultValue ? field.defaultValue : '')
                        }
                        onChange={(e) =>
                          handleInputChange(field.name, e.target.value)
                        }
                        className="w-full p-3 bg-gray-700 text-white rounded-md border border-gray-600 focus:outline-none focus:ring-2 focus:ring-cyan-500"
                      >
                        <option value="">Select {field.label}</option>
                        {field.options.map((option) => (
                          <option key={option} value={option}>
                            {option.charAt(0).toUpperCase() + option.slice(1)}
                          </option>
                        ))}
                      </select>
                    ) : null}
                  </div>
                ))}
              </div>

              {/* Writing Style Selection (Optional) */}
              {taskType === 'blog_post' && (
                <div className="mt-6 p-4 bg-gray-700 rounded-lg">
                  <h3 className="text-lg font-bold text-cyan-400 mb-4">
                    ✍️ Writing Style (Optional)
                  </h3>
                  <WritingStyleSelector
                    value={selectedWritingStyleId}
                    onChange={handleWritingStyleChange}
                    includeNone={true}
                  />
                  <p className="text-xs text-gray-400 mt-2">
                    Select a writing sample to match your voice and style
                    preferences.
                  </p>
                </div>
              )}

              {/* Model Selection Panel */}
              <div className="mt-6 p-4 bg-gray-700 rounded-lg">
                <h3 className="text-lg font-bold text-cyan-400 mb-4">
                  🤖 AI Model Configuration
                </h3>
                <div
                  className="bg-gray-800 p-4 rounded-lg"
                  style={{ maxHeight: '600px', overflowY: 'auto' }}
                >
                  <ModelSelectionPanel
                    onSelectionChange={handleModelSelectionChange}
                    initialQuality="balanced"
                  />
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex justify-end gap-4 mt-8">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={submitting}
                  className="py-2 px-6 bg-gray-600 hover:bg-gray-500 text-white font-bold rounded-lg transition duration-300 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="py-2 px-6 bg-cyan-500 hover:bg-cyan-600 text-white font-bold rounded-lg transition duration-300 disabled:opacity-50 flex items-center gap-2"
                >
                  {submitting ? (
                    <>
                      <span className="animate-spin">⟳</span> Creating...
                    </>
                  ) : (
                    '✓ Create Task'
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};

export default CreateTaskModal;
