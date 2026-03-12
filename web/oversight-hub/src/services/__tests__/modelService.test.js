/**
 * modelService.test.js
 *
 * Unit tests for services/modelService.js.
 *
 * Tests focus on pure synchronous methods (no fetch calls):
 * - modelService.getDefaultModels — returns array, all have expected fields
 * - modelService.getModel — found, not found
 * - modelService.getRecommendedModels — local models sorted first, then free, then medium
 * - modelService.getModelsForRTX5070 — filters by VRAM <= 12, cloud models pass
 * - modelService.estimateCost — unknown model, free model, gemini model
 * - modelService.getDefaultStatus — returns valid status object
 * - modelService.groupModelsByProvider — array input, maps provider aliases, unknown providers
 * - modelService.getProviderDisplayName — known providers, unknown
 * - modelService.getProviderIcon — known providers, unknown
 * - modelService.formatModelDisplayName — null, with version tag, hyphenated, underscore
 * - modelService.getModelValue — string passthrough, object without prefix, object with duplicate prefix
 * - modelService.parseModelValue — empty, single-part, multi-part
 *
 * getAvailableModels and getProviderStatus make fetch calls — tested only for fallback behavior.
 */

import { vi } from 'vitest';

vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

vi.mock('../config/apiConfig', () => ({
  getApiUrl: () => 'http://localhost:8000',
}));

// Use the singleton exported from the module
import { modelService } from '../modelService';

// Reload models from defaults before each test
beforeEach(() => {
  modelService.models = modelService.getDefaultModels();
});

// ---------------------------------------------------------------------------
// getDefaultModels
// ---------------------------------------------------------------------------

describe('getDefaultModels', () => {
  it('returns an array of models', () => {
    const models = modelService.getDefaultModels();
    expect(Array.isArray(models)).toBe(true);
    expect(models.length).toBeGreaterThan(0);
  });

  it('all models have required fields', () => {
    const models = modelService.getDefaultModels();
    for (const model of models) {
      expect(model).toHaveProperty('name');
      expect(model).toHaveProperty('provider');
      expect(model).toHaveProperty('isFree');
    }
  });

  it('includes at least one ollama model', () => {
    const models = modelService.getDefaultModels();
    expect(models.some((m) => m.provider === 'ollama')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// getModel
// ---------------------------------------------------------------------------

describe('getModel', () => {
  it('returns model when found by name', () => {
    const result = modelService.getModel('neural-chat:13b');
    expect(result).toBeDefined();
    expect(result.name).toBe('neural-chat:13b');
  });

  it('returns undefined for unknown model', () => {
    const result = modelService.getModel('does-not-exist');
    expect(result).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// getRecommendedModels
// ---------------------------------------------------------------------------

describe('getRecommendedModels', () => {
  it('returns array with local models first', () => {
    const models = modelService.getRecommendedModels();
    // First model should be ollama (local)
    expect(models[0].provider).toBe('ollama');
  });

  it('free models before paid within same provider type', () => {
    modelService.models = [
      {
        name: 'paid-cloud',
        provider: 'openai',
        isFree: false,
        size: 'medium',
        estimatedVramGb: 0,
      },
      {
        name: 'free-cloud',
        provider: 'openai',
        isFree: true,
        size: 'medium',
        estimatedVramGb: 0,
      },
    ];
    const result = modelService.getRecommendedModels();
    expect(result[0].isFree).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// getModelsForRTX5070
// ---------------------------------------------------------------------------

describe('getModelsForRTX5070', () => {
  it('filters out ollama models over 12GB VRAM', () => {
    modelService.models = [
      { name: 'small', provider: 'ollama', estimatedVramGb: 7 },
      { name: 'large', provider: 'ollama', estimatedVramGb: 24 },
    ];
    const result = modelService.getModelsForRTX5070();
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('small');
  });

  it('always includes cloud models regardless of vram', () => {
    modelService.models = [
      { name: 'cloud-model', provider: 'openai', estimatedVramGb: 0 },
    ];
    const result = modelService.getModelsForRTX5070();
    expect(result).toHaveLength(1);
  });

  it('includes ollama models at exactly 12GB', () => {
    modelService.models = [
      { name: 'exact', provider: 'ollama', estimatedVramGb: 12 },
    ];
    const result = modelService.getModelsForRTX5070();
    expect(result).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// estimateCost
// ---------------------------------------------------------------------------

describe('estimateCost', () => {
  it('returns 0 for unknown model', () => {
    const cost = modelService.estimateCost('nonexistent', 1000000);
    expect(cost).toBe(0);
  });

  it('returns 0 for free ollama model', () => {
    // neural-chat:13b is ollama (free)
    const cost = modelService.estimateCost('neural-chat:13b', 1000000);
    expect(cost).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// getDefaultStatus
// ---------------------------------------------------------------------------

describe('getDefaultStatus', () => {
  it('returns status with ollama, huggingface, gemini providers', () => {
    const status = modelService.getDefaultStatus();
    expect(status.ollama).toBeDefined();
    expect(status.huggingface).toBeDefined();
    expect(status.gemini).toBeDefined();
  });

  it('all providers have available: false by default', () => {
    const status = modelService.getDefaultStatus();
    expect(status.ollama.available).toBe(false);
    expect(status.huggingface.available).toBe(false);
    expect(status.gemini.available).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// groupModelsByProvider
// ---------------------------------------------------------------------------

describe('groupModelsByProvider', () => {
  it('groups models by provider', () => {
    const models = [
      { name: 'llama2', provider: 'ollama' },
      { name: 'gpt-4', provider: 'openai' },
      { name: 'claude', provider: 'anthropic' },
    ];
    const result = modelService.groupModelsByProvider(models);
    expect(result.ollama).toHaveLength(1);
    expect(result.openai).toHaveLength(1);
    expect(result.anthropic).toHaveLength(1);
  });

  it('maps gemini provider to google group', () => {
    const models = [{ name: 'gemini-pro', provider: 'gemini' }];
    const result = modelService.groupModelsByProvider(models);
    expect(result.google).toHaveLength(1);
  });

  it('uses this.models when no argument passed', () => {
    const result = modelService.groupModelsByProvider();
    // Default models contain ollama models
    expect(result.ollama.length).toBeGreaterThan(0);
  });

  it('ignores models with unknown providers', () => {
    const models = [{ name: 'custom-model', provider: 'unknown_provider' }];
    const result = modelService.groupModelsByProvider(models);
    // Should not throw, and known providers should still exist
    expect(result.ollama).toBeDefined();
    expect(result.openai).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// getProviderDisplayName
// ---------------------------------------------------------------------------

describe('getProviderDisplayName', () => {
  it('returns display name for ollama', () => {
    expect(modelService.getProviderDisplayName('ollama')).toContain('Ollama');
  });

  it('returns display name for openai', () => {
    expect(modelService.getProviderDisplayName('openai')).toContain('OpenAI');
  });

  it('returns raw provider for unknown', () => {
    expect(modelService.getProviderDisplayName('custom_ai')).toBe('custom_ai');
  });

  it('is case-insensitive', () => {
    expect(modelService.getProviderDisplayName('OLLAMA')).toContain('Ollama');
  });
});

// ---------------------------------------------------------------------------
// getProviderIcon
// ---------------------------------------------------------------------------

describe('getProviderIcon', () => {
  it('returns icon for ollama', () => {
    expect(modelService.getProviderIcon('ollama')).toBe('🖥️');
  });

  it('returns icon for openai', () => {
    expect(modelService.getProviderIcon('openai')).toBe('⚡');
  });

  it('returns default robot icon for unknown provider', () => {
    expect(modelService.getProviderIcon('unknown')).toBe('🤖');
  });
});

// ---------------------------------------------------------------------------
// formatModelDisplayName
// ---------------------------------------------------------------------------

describe('formatModelDisplayName', () => {
  it('returns Unknown for null', () => {
    expect(modelService.formatModelDisplayName(null)).toBe('Unknown');
  });

  it('returns Unknown for empty string', () => {
    expect(modelService.formatModelDisplayName('')).toBe('Unknown');
  });

  it('strips version tag after colon', () => {
    const result = modelService.formatModelDisplayName('llama2:13b');
    expect(result).toBe('Llama2');
  });

  it('capitalizes hyphenated words', () => {
    const result = modelService.formatModelDisplayName('neural-chat');
    expect(result).toBe('Neural Chat');
  });

  it('capitalizes underscore-separated words', () => {
    const result = modelService.formatModelDisplayName('my_model');
    expect(result).toBe('My Model');
  });

  it('handles single word', () => {
    expect(modelService.formatModelDisplayName('mistral')).toBe('Mistral');
  });
});

// ---------------------------------------------------------------------------
// getModelValue
// ---------------------------------------------------------------------------

describe('getModelValue', () => {
  it('returns string directly if model is a string', () => {
    expect(modelService.getModelValue('ollama-mistral')).toBe('ollama-mistral');
  });

  it('returns model name when name already has provider prefix', () => {
    // gemini-2.5-flash starts with "gemini" which is the provider
    const model = { name: 'gemini-2.5-flash', provider: 'google' };
    // provider 'google' maps to 'gemini', name starts with 'gemini'
    expect(modelService.getModelValue(model)).toBe('gemini-2.5-flash');
  });

  it('prepends provider to model name when no prefix', () => {
    const model = { name: 'mistral', provider: 'ollama' };
    expect(modelService.getModelValue(model)).toBe('ollama-mistral');
  });

  it('maps google provider to gemini', () => {
    const model = { name: 'my-model', provider: 'google' };
    expect(modelService.getModelValue(model)).toBe('gemini-my-model');
  });
});

// ---------------------------------------------------------------------------
// parseModelValue
// ---------------------------------------------------------------------------

describe('parseModelValue', () => {
  it('returns default for empty string', () => {
    const result = modelService.parseModelValue('');
    expect(result).toEqual({ provider: 'ollama', model: 'default' });
  });

  it('returns default for null', () => {
    const result = modelService.parseModelValue(null);
    expect(result).toEqual({ provider: 'ollama', model: 'default' });
  });

  it('returns ollama provider for single-part value', () => {
    const result = modelService.parseModelValue('mistral');
    expect(result).toEqual({ provider: 'ollama', model: 'mistral' });
  });

  it('splits provider from model for multi-part value', () => {
    const result = modelService.parseModelValue('ollama-mistral');
    expect(result.provider).toBe('ollama');
    expect(result.model).toBe('mistral');
  });

  it('handles multi-hyphen model names correctly', () => {
    const result = modelService.parseModelValue('anthropic-claude-3-sonnet');
    expect(result.provider).toBe('anthropic');
    expect(result.model).toBe('claude-3-sonnet');
  });
});

// ---------------------------------------------------------------------------
// getAvailableModels — fallback behavior
// ---------------------------------------------------------------------------

describe('getAvailableModels (fallback behavior)', () => {
  it('returns default models when fetch fails', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));
    const result = await modelService.getAvailableModels();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns default models when fetch response is not ok', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({}),
    });
    const result = await modelService.getAvailableModels();
    expect(result.some((m) => m.provider === 'ollama')).toBe(true);
  });
});
