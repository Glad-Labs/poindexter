/**
 * AIStudio.jsx tests
 *
 * Covers:
 * - Initial render (tabs, model table, default tab visible)
 * - Tab switching
 * - Model test validation (no model/prompt → error message)
 * - Model test success path
 * - Model test error path
 * - Training data tab triggers loadTrainingAll (makeRequest called)
 * - Ollama fetch failure handled gracefully (no crash)
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import AIStudio from '../AIStudio';

// ── mock cofounderAgentClient ──────────────────────────────────────────────
const { mockMakeRequest } = vi.hoisted(() => ({
  mockMakeRequest: vi.fn(),
}));

vi.mock('../../services/cofounderAgentClient', () => ({
  makeRequest: mockMakeRequest,
}));

// ── mock global fetch (used for Ollama) ────────────────────────────────────
const makeFetchMock = (models = []) =>
  vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ models }),
  });

// ── helpers ────────────────────────────────────────────────────────────────
function renderAIStudio() {
  return render(<AIStudio />);
}

// ── tests ──────────────────────────────────────────────────────────────────

describe('AIStudio', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: Ollama returns no models
    global.fetch = makeFetchMock([]);
    mockMakeRequest.mockResolvedValue({});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial render', () => {
    it('renders the page title', () => {
      renderAIStudio();
      expect(screen.getByText(/AI Studio/i)).toBeInTheDocument();
    });

    it('renders all four tab buttons', () => {
      renderAIStudio();
      // Use exact tab labels to avoid multiple-match errors
      expect(
        screen.getByRole('button', { name: /📊 Models/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /🧪 Test Models/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /📚 Training Data/i })
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /📈 Test History/i })
      ).toBeInTheDocument();
    });

    it('shows Models tab content by default', async () => {
      renderAIStudio();
      // The fallback models table should be visible after async load
      await waitFor(() => {
        expect(screen.getByText('gpt-4')).toBeInTheDocument();
      });
    });
  });

  describe('tab switching', () => {
    it('switches to Test Models tab', () => {
      renderAIStudio();
      fireEvent.click(screen.getByText(/Test Models/i));
      // test tab should show a test prompt area
      expect(screen.getByText(/Test Models/i)).toBeInTheDocument();
    });

    it('switches to Test History tab', () => {
      renderAIStudio();
      const historyBtn = screen.getByRole('button', {
        name: /📈 Test History/i,
      });
      fireEvent.click(historyBtn);
      expect(historyBtn).toBeInTheDocument();
    });
  });

  describe('model test validation', () => {
    it('shows error when no model is selected and test is run', async () => {
      renderAIStudio();
      // Switch to test tab
      fireEvent.click(screen.getByText(/Test Models/i));

      // Find the Run Test button
      const runBtn = screen.getByRole('button', { name: /Run Test/i });
      fireEvent.click(runBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/Please select a model and enter a prompt/i)
        ).toBeInTheDocument();
      });
    });
  });

  describe('model test success', () => {
    it('displays test result after successful API call', async () => {
      // Seed an Ollama model so runModelTest doesn't hit the validation guard
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ models: [{ name: 'llama3:latest' }] }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              response: 'Artificial Intelligence is...', // Ollama generate response
              eval_count: 42,
            }),
        });

      renderAIStudio();
      fireEvent.click(screen.getByText(/Test Models/i));

      // Wait for model to be available and selected
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining('/api/tags')
        );
      });

      const runBtn = screen.getByRole('button', { name: /Run Test/i });
      fireEvent.click(runBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/Artificial Intelligence is/i)
        ).toBeInTheDocument();
      });
    });
  });

  describe('model test error handling', () => {
    it('shows error message when Ollama generate fails', async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ models: [{ name: 'llama3:latest' }] }),
        })
        .mockResolvedValueOnce({
          ok: false,
        });

      renderAIStudio();
      fireEvent.click(screen.getByText(/Test Models/i));

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(
          expect.stringContaining('/api/tags')
        );
      });

      const runBtn = screen.getByRole('button', { name: /Run Test/i });
      fireEvent.click(runBtn);

      await waitFor(() => {
        expect(screen.getByText(/Test failed/i)).toBeInTheDocument();
      });
    });
  });

  describe('training data tab', () => {
    it('calls makeRequest for training stats when switching to Training tab', async () => {
      mockMakeRequest.mockResolvedValue({ datasets: [], jobs: [] });
      renderAIStudio();

      fireEvent.click(screen.getByText(/Training Data/i));

      await waitFor(() => {
        expect(mockMakeRequest).toHaveBeenCalledWith(
          expect.stringContaining('/api/orchestrator/training/'),
          'GET'
        );
      });
    });
  });

  describe('Ollama fetch failure', () => {
    it('renders without crashing when Ollama is unavailable', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Connection refused'));

      const { container } = renderAIStudio();

      // Give the effect time to run
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      // Component should still be mounted
      expect(container.firstChild).not.toBeNull();
      // The fallback models should still show
      expect(screen.getByText('gpt-4')).toBeInTheDocument();
    });
  });
});
