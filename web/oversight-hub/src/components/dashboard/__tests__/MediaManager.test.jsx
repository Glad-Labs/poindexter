/**
 * Tests for components/dashboard/MediaManager.jsx
 *
 * Covers:
 * - Renders Media Manager heading
 * - Renders Generate Image and Media Gallery tabs
 * - Generate Image tab is shown by default with form fields
 * - Error when generate clicked with empty prompt
 * - Successful image generation shows success
 * - Switching to Media Gallery loads media list
 * - Health check button behavior
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock mediaService — vi.hoisted() required so variables are available in factory
const {
  mockGenerateImages,
  mockListMedia,
  mockDeleteMedia,
  mockGetMediaHealth,
} = vi.hoisted(() => ({
  mockGenerateImages: vi.fn(),
  mockListMedia: vi.fn(),
  mockDeleteMedia: vi.fn(),
  mockGetMediaHealth: vi.fn(),
}));

vi.mock('../../../services/mediaService', () => ({
  generateImages: mockGenerateImages,
  listMedia: mockListMedia,
  deleteMedia: mockDeleteMedia,
  getMediaHealth: mockGetMediaHealth,
}));

import { MediaManager } from '../MediaManager';

describe('MediaManager — base render', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListMedia.mockResolvedValue({ media: [] });
    mockGenerateImages.mockResolvedValue({ success: true, images: [] });
    mockGetMediaHealth.mockResolvedValue({ healthy: true });
  });

  it('renders Media Manager heading', () => {
    render(<MediaManager />);
    expect(screen.getByText('Media Manager')).toBeInTheDocument();
  });

  it('renders Generate Image and Media Gallery tabs', () => {
    render(<MediaManager />);
    expect(
      screen.getByRole('tab', { name: /Generate Image/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /Media Gallery/i })
    ).toBeInTheDocument();
  });

  it('shows Generate Featured Image card by default', () => {
    render(<MediaManager />);
    expect(screen.getByText('Generate Featured Image')).toBeInTheDocument();
  });

  it('renders Image Prompt text field', () => {
    render(<MediaManager />);
    expect(screen.getByLabelText(/Image Prompt/i)).toBeInTheDocument();
  });

  it('renders Post Title optional text field', () => {
    render(<MediaManager />);
    expect(screen.getByLabelText(/Post Title/i)).toBeInTheDocument();
  });

  it('Generate Image button is disabled when prompt is empty', () => {
    render(<MediaManager />);
    const generateBtn = screen.getByRole('button', {
      name: /^Generate Image$/i,
    });
    expect(generateBtn).toBeDisabled();
  });

  it('Generate Image button is enabled when prompt has content', () => {
    render(<MediaManager />);
    const promptField = screen.getByLabelText(/Image Prompt/i);
    fireEvent.change(promptField, {
      target: { value: 'AI futuristic cityscape' },
    });
    const generateBtn = screen.getByRole('button', {
      name: /^Generate Image$/i,
    });
    expect(generateBtn).not.toBeDisabled();
  });
});

describe('MediaManager — image generation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListMedia.mockResolvedValue({ media: [] });
    mockGenerateImages.mockResolvedValue({ success: true, images: ['url1'] });
  });

  it('calls generateImages when button clicked with valid prompt', async () => {
    render(<MediaManager />);
    const promptField = screen.getByLabelText(/Image Prompt/i);
    fireEvent.change(promptField, { target: { value: 'Mountains at sunset' } });

    fireEvent.click(screen.getByRole('button', { name: /Generate Image/i }));

    await waitFor(() => {
      expect(mockGenerateImages).toHaveBeenCalledWith(
        expect.objectContaining({
          prompt: 'Mountains at sunset',
          use_pexels: true,
        })
      );
    });
  });

  it('shows success alert after successful generation', async () => {
    render(<MediaManager />);
    const promptField = screen.getByLabelText(/Image Prompt/i);
    fireEvent.change(promptField, { target: { value: 'Futuristic city' } });

    fireEvent.click(screen.getByRole('button', { name: /Generate Image/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Operation completed successfully/i)
      ).toBeInTheDocument();
    });
  });

  it('shows error alert when generateImages returns failure', async () => {
    mockGenerateImages.mockResolvedValue({
      success: false,
      message: 'API quota exceeded',
    });
    render(<MediaManager />);
    const promptField = screen.getByLabelText(/Image Prompt/i);
    fireEvent.change(promptField, { target: { value: 'Test prompt' } });
    fireEvent.click(screen.getByRole('button', { name: /Generate Image/i }));

    await waitFor(() => {
      expect(screen.getByText('API quota exceeded')).toBeInTheDocument();
    });
  });

  it('shows error alert when generateImages throws', async () => {
    mockGenerateImages.mockRejectedValue(new Error('Network failure'));
    render(<MediaManager />);
    const promptField = screen.getByLabelText(/Image Prompt/i);
    fireEvent.change(promptField, { target: { value: 'Test prompt' } });
    fireEvent.click(screen.getByRole('button', { name: /Generate Image/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Failed to generate image: Network failure/i)
      ).toBeInTheDocument();
    });
  });
});

describe('MediaManager — gallery tab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListMedia.mockResolvedValue({
      media: [
        {
          id: 'media-1',
          title: 'Hero Image',
          url: 'https://example.com/hero.jpg',
          created_at: '2026-03-01T00:00:00Z',
        },
      ],
    });
    mockGetMediaHealth.mockResolvedValue({ healthy: true });
  });

  it('switches to Media Gallery tab and loads media', async () => {
    render(<MediaManager />);
    fireEvent.click(screen.getByRole('tab', { name: /Media Gallery/i }));

    await waitFor(() => {
      expect(mockListMedia).toHaveBeenCalledWith({ limit: 50 });
    });
  });

  it('shows media items in gallery tab', async () => {
    render(<MediaManager />);
    fireEvent.click(screen.getByRole('tab', { name: /Media Gallery/i }));

    await waitFor(() => {
      // Component renders item.title || item.id
      expect(screen.getByText('Hero Image')).toBeInTheDocument();
    });
  });

  it('shows empty state when no media', async () => {
    mockListMedia.mockResolvedValue({ media: [] });
    render(<MediaManager />);
    fireEvent.click(screen.getByRole('tab', { name: /Media Gallery/i }));

    await waitFor(() => {
      expect(screen.getByText(/No media found/i)).toBeInTheDocument();
    });
  });
});

describe('MediaManager — health check', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListMedia.mockResolvedValue({ media: [] });
    mockGetMediaHealth.mockResolvedValue({ healthy: true });
  });

  it('renders Check Health button', () => {
    render(<MediaManager />);
    expect(
      screen.getByRole('button', { name: /Check Health/i })
    ).toBeInTheDocument();
  });

  it('shows success when health check passes', async () => {
    render(<MediaManager />);
    fireEvent.click(screen.getByRole('button', { name: /Check Health/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Operation completed successfully/i)
      ).toBeInTheDocument();
    });
  });

  it('shows error when health check returns unhealthy', async () => {
    mockGetMediaHealth.mockResolvedValue({ healthy: false });
    render(<MediaManager />);
    fireEvent.click(screen.getByRole('button', { name: /Check Health/i }));

    await waitFor(() => {
      expect(
        screen.getByText('Media service is not healthy')
      ).toBeInTheDocument();
    });
  });
});
