import logger from '@/lib/logger';
/**
 * Model Management Service
 *
 * Provides model availability, selection, and information to the UI
 */

import { getApiUrl } from '../config/apiConfig';
import { getAuthToken } from './authService';

class ModelService {
  constructor() {
    this.models = [];
    this.selectedModel = null;
    this.loadingModels = false;
  }

  /**
   * Get available models from the backend
   */
  async getAvailableModels() {
    try {
      const API_BASE_URL = getApiUrl();
      const token = getAuthToken();
      const response = await fetch(`${API_BASE_URL}/api/v1/models/available`, {
        headers: {
          'Content-Type': 'application/json',
          ...(token && { Authorization: `Bearer ${token}` }),
          Accept: 'application/json',
        },
        credentials: 'include',
      });

      if (!response.ok) {
        if (process.env.NODE_ENV !== 'production') {
          logger.warn('Could not fetch available models, using defaults');
        }
        return this.getDefaultModels();
      }

      const data = await response.json();
      this.models = data.models || this.getDefaultModels();
      return this.models;
    } catch (error) {
      if (process.env.NODE_ENV !== 'production') {
        logger.warn('Error fetching models:', error);
      }
      return this.getDefaultModels();
    }
  }

  /**
   * Get model details by name
   */
  getModel(modelName) {
    return this.models.find((m) => m.name === modelName);
  }

  /**
   * Get recommended models (sorted by preference)
   */
  getRecommendedModels() {
    // Sort by: local first, free, medium size
    return this.models.sort((a, b) => {
      // Local (Ollama) first
      const aLocal = a.provider === 'ollama' ? 0 : 1;
      const bLocal = b.provider === 'ollama' ? 0 : 1;
      if (aLocal !== bLocal) {
        return aLocal - bLocal;
      }

      // Free models first
      const aFree = a.isFree ? 0 : 1;
      const bFree = b.isFree ? 0 : 1;
      if (aFree !== bFree) {
        return aFree - bFree;
      }

      // Prefer medium models
      const sizeOrder = { small: 1, medium: 0, large: 2 };
      return (sizeOrder[a.size] || 2) - (sizeOrder[b.size] || 2);
    });
  }

  /**
   * Get models suitable for RTX 5070
   */
  getModelsForRTX5070() {
    return this.models.filter((m) => {
      // RTX 5070 has 12GB VRAM
      if (m.provider === 'ollama') {
        return m.estimatedVramGb <= 12;
      }
      return true; // Cloud models don't use local VRAM
    });
  }

  /**
   * Get provider status
   */
  async getProviderStatus() {
    try {
      const API_BASE_URL = getApiUrl();
      const response = await fetch(`${API_BASE_URL}/api/models/status`, {
        headers: {
          Accept: 'application/json',
        },
      });

      if (!response.ok) {
        return this.getDefaultStatus();
      }

      return await response.json();
    } catch (error) {
      if (process.env.NODE_ENV !== 'production') {
        logger.warn('Error fetching provider status:', error);
      }
      return this.getDefaultStatus();
    }
  }

  /**
   * Get cost estimate for a model
   */
  estimateCost(modelName, tokenCount = 1000000) {
    const model = this.getModel(modelName);
    if (!model) {
      return 0;
    }

    // Based on typical pricing
    const pricePerMillionTokens = {
      'gemini-2.5-flash': 0.05, // $0.05 per 1M input tokens
      ollama: 0, // Local, free
      huggingface: 0, // Free tier
    };

    const price = pricePerMillionTokens[model.provider] || 0;
    return (price * tokenCount) / 1000000;
  }

  /**
   * Get default models (client-side fallback)
   */
  getDefaultModels() {
    return [
      {
        name: 'neural-chat:13b',
        displayName: 'Neural Chat 13B (Local)',
        provider: 'ollama',
        isFree: true,
        size: 'large',
        estimatedVramGb: 12,
        description: 'Excellent for blog generation. Optimized for RTX 5070.',
        icon: '🖥️',
      },
      {
        name: 'mistral:13b',
        displayName: 'Mistral 13B (Local)',
        provider: 'ollama',
        isFree: true,
        size: 'large',
        estimatedVramGb: 12,
        description: 'High-quality model. Great for RTX 5070.',
        icon: '🖥️',
      },
      {
        name: 'neural-chat:7b',
        displayName: 'Neural Chat 7B (Local)',
        provider: 'ollama',
        isFree: true,
        size: 'medium',
        estimatedVramGb: 7,
        description: 'Fast and good quality. Works on smaller GPUs.',
        icon: '🖥️',
      },
      {
        name: 'mistralai/Mistral-7B-Instruct-v0.1',
        displayName: 'Mistral 7B (HuggingFace)',
        provider: 'huggingface',
        isFree: true,
        size: 'medium',
        estimatedVramGb: 0,
        description: 'Free tier available. Requires HF token.',
        icon: '🌐',
      },
      {
        name: 'gemini-2.5-flash',
        displayName: 'Gemini 2.5 Flash (Google)',
        provider: 'gemini',
        isFree: false,
        size: 'large',
        estimatedVramGb: 0,
        description: 'Reliable fallback. ~$0.05 per 1M tokens.',
        icon: '☁️',
      },
    ];
  }

  /**
   * Get default provider status
   */
  getDefaultStatus() {
    return {
      ollama: {
        available: false,
        url: 'http://localhost:11434',
        models: 0,
      },
      huggingface: {
        available: false,
        hasToken: false,
        models: 0,
      },
      gemini: {
        available: false,
        hasKey: false,
        models: 0,
      },
    };
  }

  /**
   * Group models by provider for UI display
   * @param {Array} models - Array of model objects
   * @returns {Object} Grouped models {ollama: [...], openai: [...], anthropic: [...], google: [...]}
   */
  groupModelsByProvider(models = null) {
    const modelsToGroup = models || this.models;
    const grouped = {
      ollama: [],
      openai: [],
      anthropic: [],
      google: [],
      huggingface: [],
      gemini: [],
    };

    modelsToGroup.forEach((model) => {
      const provider = (model.provider || 'ollama').toLowerCase();

      // Map common provider names to our keys
      const providerMap = {
        openai: 'openai',
        gpt: 'openai',
        anthropic: 'anthropic',
        claude: 'anthropic',
        google: 'google',
        gemini: 'google',
        ollama: 'ollama',
        huggingface: 'huggingface',
      };

      const mappedProvider = providerMap[provider] || provider;

      if (Object.prototype.hasOwnProperty.call(grouped, mappedProvider)) {
        grouped[mappedProvider].push(model);
      }
    });

    return grouped;
  }

  /**
   * Get display name for a provider
   * @param {string} provider - Provider identifier
   * @returns {string} Display name with icon
   */
  getProviderDisplayName(provider) {
    const names = {
      ollama: '🖥️ Ollama (Local)',
      openai: '⚡ OpenAI',
      anthropic: '🧠 Anthropic',
      google: '☁️ Google',
      huggingface: '🌐 Hugging Face',
      gemini: '☁️ Google Gemini',
    };
    return names[provider.toLowerCase()] || provider;
  }

  /**
   * Get provider icon emoji
   * @param {string} provider - Provider identifier
   * @returns {string} Emoji icon
   */
  getProviderIcon(provider) {
    const icons = {
      ollama: '🖥️',
      openai: '⚡',
      anthropic: '🧠',
      google: '☁️',
      gemini: '☁️',
      huggingface: '🌐',
    };
    return icons[provider.toLowerCase()] || '🤖';
  }

  /**
   * Format model name for display
   * @param {string} modelName - Model name from API
   * @returns {string} Formatted display name
   */
  formatModelDisplayName(modelName) {
    if (!modelName) return 'Unknown';

    // Remove version tags like ":latest", ":7b", etc.
    let cleaned = modelName.split(':')[0];

    // Capitalize first letter of each word
    cleaned = cleaned
      .split(/[-_]/)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');

    return cleaned;
  }

  /**
   * Get model value in "provider-model" format
   * @param {Object} model - Model object or string
   * @returns {string} Value (e.g., "ollama-mistral")
   */
  getModelValue(model) {
    if (typeof model === 'string') return model;
    let provider = (model.provider || 'ollama').toLowerCase();
    // Map 'google' to 'gemini' for backend API compatibility
    if (provider === 'google') {
      provider = 'gemini';
    }
    const name = model.name || model.id || 'default';

    // Don't duplicate provider prefix - if name already starts with provider, just return name
    // e.g., if provider='gemini' and name='gemini-2.5-flash', return 'gemini-2.5-flash'
    // not 'gemini-gemini-2.5-flash'
    const nameProviderPrefix = name.toLowerCase().split('-')[0];
    if (nameProviderPrefix === provider) {
      return name; // Name already has provider prefix
    }

    return `${provider}-${name}`;
  }

  /**
   * Parse model value to extract provider and model name
   * @param {string} value - Value (e.g., "ollama-mistral" or "mistral")
   * @returns {Object} {provider: string, model: string}
   */
  parseModelValue(value) {
    if (!value) return { provider: 'ollama', model: 'default' };

    const parts = value.split('-');
    if (parts.length === 1) {
      return { provider: 'ollama', model: parts[0] };
    }

    return { provider: parts[0], model: parts.slice(1).join('-') };
  }
}

// Export as singleton
export const modelService = new ModelService();
