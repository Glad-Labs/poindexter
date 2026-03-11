import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import ModelSelectDropdown from '../ModelSelectDropdown';

// Mock modelService
vi.mock('../../services/modelService', () => ({
  modelService: {
    getModelValue: vi.fn((m) => m.name || m.id || ''),
    formatModelDisplayName: vi.fn((name) => name),
  },
}));

// Mock logger to avoid noise
vi.mock('@/lib/logger', () => ({
  default: {
    debug: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

describe('ModelSelectDropdown Component', () => {
  const mockOnChange = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Empty / no models state', () => {
    it('renders a disabled select with "No models available" when modelsByProvider is empty', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={{}}
        />
      );
      const select = screen.getByRole('combobox');
      expect(select).toBeDisabled();
      expect(screen.getByText('No models available')).toBeInTheDocument();
    });

    it('renders disabled select when modelsByProvider is not provided', () => {
      render(<ModelSelectDropdown value="" onChange={mockOnChange} />);
      const select = screen.getByRole('combobox');
      expect(select).toBeDisabled();
    });
  });

  describe('With models', () => {
    const modelsByProvider = {
      ollama: [{ name: 'llama2', displayName: 'Llama 2' }],
      openai: [{ name: 'gpt-4', displayName: 'GPT-4' }],
      anthropic: [{ name: 'claude-3', displayName: 'Claude 3' }],
    };

    it('renders an enabled select when models are available', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      const select = screen.getByRole('combobox');
      expect(select).not.toBeDisabled();
    });

    it('renders the placeholder option "-- Select Model --"', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      expect(screen.getByText('-- Select Model --')).toBeInTheDocument();
    });

    it('renders Ollama optgroup when ollama models exist', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      // optgroup label contains "Ollama"
      const optgroups = document.querySelectorAll('optgroup');
      const ollamaGroup = Array.from(optgroups).find((g) =>
        g.label.includes('Ollama')
      );
      expect(ollamaGroup).toBeTruthy();
    });

    it('renders OpenAI optgroup when openai models exist', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      const optgroups = document.querySelectorAll('optgroup');
      const openaiGroup = Array.from(optgroups).find((g) =>
        g.label.includes('OpenAI')
      );
      expect(openaiGroup).toBeTruthy();
    });

    it('renders Anthropic optgroup when anthropic models exist', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      const optgroups = document.querySelectorAll('optgroup');
      const anthropicGroup = Array.from(optgroups).find((g) =>
        g.label.includes('Anthropic')
      );
      expect(anthropicGroup).toBeTruthy();
    });

    it('does not render Google optgroup when no google models', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      const optgroups = document.querySelectorAll('optgroup');
      const googleGroup = Array.from(optgroups).find((g) =>
        g.label.includes('Google')
      );
      expect(googleGroup).toBeFalsy();
    });

    it('renders Google optgroup when google models exist', () => {
      const withGoogle = {
        ...modelsByProvider,
        google: [{ name: 'gemini-pro', displayName: 'Gemini Pro' }],
      };
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={withGoogle}
        />
      );
      const optgroups = document.querySelectorAll('optgroup');
      const googleGroup = Array.from(optgroups).find((g) =>
        g.label.includes('Google')
      );
      expect(googleGroup).toBeTruthy();
    });

    it('renders HuggingFace optgroup when huggingface models exist', () => {
      const withHF = {
        huggingface: [{ name: 'bert-base', displayName: 'BERT Base' }],
      };
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={withHF}
        />
      );
      const optgroups = document.querySelectorAll('optgroup');
      const hfGroup = Array.from(optgroups).find((g) =>
        g.label.includes('HuggingFace')
      );
      expect(hfGroup).toBeTruthy();
    });

    it('calls onChange with the selected model value when changed', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      const select = screen.getByRole('combobox');
      fireEvent.change(select, { target: { value: 'llama2' } });
      expect(mockOnChange).toHaveBeenCalledWith('llama2');
    });

    it('respects the disabled prop when models are available', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
          disabled={true}
        />
      );
      expect(screen.getByRole('combobox')).toBeDisabled();
    });

    it('shows the current value as selected', () => {
      render(
        <ModelSelectDropdown
          value="gpt-4"
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
        />
      );
      const select = screen.getByRole('combobox');
      expect(select.value).toBe('gpt-4');
    });

    it('applies custom className to the select element', () => {
      render(
        <ModelSelectDropdown
          value=""
          onChange={mockOnChange}
          modelsByProvider={modelsByProvider}
          className="my-custom-class"
        />
      );
      const select = screen.getByRole('combobox');
      expect(select.className).toContain('my-custom-class');
    });
  });
});
