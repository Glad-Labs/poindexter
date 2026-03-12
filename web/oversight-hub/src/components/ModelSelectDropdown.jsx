import React from 'react';
import { modelService } from '../services/modelService';

/**
 * Reusable Model Selection Dropdown Component (HTML version)
 *
 * Eliminates duplication of model dropdown across multiple pages.
 * Works with both standard HTML select and Material-UI Select components.
 *
 * Props:
 *   value: Selected model value
 *   onChange: Callback when selection changes
 *   modelsByProvider: Object with provider keys (ollama, openai, etc.) containing model arrays
 *   className: Optional CSS class
 *   disabled: Optional disable state
 */
export function ModelSelectDropdown({
  value,
  onChange,
  modelsByProvider = {},
  className = '',
  disabled = false,
  'aria-label': ariaLabel = 'Select AI model',
}) {
  if (!modelsByProvider || Object.keys(modelsByProvider).length === 0) {
    return (
      <select
        value=""
        onChange={(e) => onChange(e.target.value)}
        disabled
        className={className}
        aria-label={ariaLabel}
      >
        <option value="">No models available</option>
      </select>
    );
  }

  return (
    <select
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={className}
      aria-label={ariaLabel}
    >
      <option value="">-- Select Model --</option>

      {/* Ollama Models Group */}
      {modelsByProvider.ollama && modelsByProvider.ollama.length > 0 && (
        <optgroup label="🖥️  Ollama (Local)">
          {modelsByProvider.ollama.map((m) => (
            <option
              key={modelService.getModelValue(m)}
              value={modelService.getModelValue(m)}
            >
              {modelService.formatModelDisplayName(m.name || m.displayName)}
            </option>
          ))}
        </optgroup>
      )}

      {/* OpenAI Models Group */}
      {modelsByProvider.openai && modelsByProvider.openai.length > 0 && (
        <optgroup label="⚡ OpenAI">
          {modelsByProvider.openai.map((m) => (
            <option
              key={modelService.getModelValue(m)}
              value={modelService.getModelValue(m)}
            >
              {m.displayName || modelService.formatModelDisplayName(m.name)}
            </option>
          ))}
        </optgroup>
      )}

      {/* Anthropic Models Group */}
      {modelsByProvider.anthropic && modelsByProvider.anthropic.length > 0 && (
        <optgroup label="🧠 Anthropic">
          {modelsByProvider.anthropic.map((m) => (
            <option
              key={modelService.getModelValue(m)}
              value={modelService.getModelValue(m)}
            >
              {m.displayName || modelService.formatModelDisplayName(m.name)}
            </option>
          ))}
        </optgroup>
      )}

      {/* Google Models Group */}
      {modelsByProvider.google && modelsByProvider.google.length > 0 && (
        <optgroup label="☁️ Google">
          {modelsByProvider.google.map((m) => (
            <option
              key={modelService.getModelValue(m)}
              value={modelService.getModelValue(m)}
            >
              {m.displayName || modelService.formatModelDisplayName(m.name)}
            </option>
          ))}
        </optgroup>
      )}

      {/* HuggingFace Models Group */}
      {modelsByProvider.huggingface &&
        modelsByProvider.huggingface.length > 0 && (
          <optgroup label="🌐 HuggingFace">
            {modelsByProvider.huggingface.map((m) => (
              <option
                key={modelService.getModelValue(m)}
                value={modelService.getModelValue(m)}
              >
                {m.displayName || modelService.formatModelDisplayName(m.name)}
              </option>
            ))}
          </optgroup>
        )}
    </select>
  );
}

export default ModelSelectDropdown;
