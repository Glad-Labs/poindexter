import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import WritingStyleManager from './WritingStyleManager';
import * as writingStyleService from '../services/writingStyleService';

vi.mock('../services/writingStyleService');

describe('WritingStyleManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('renders uploaded samples without validateDOMNesting warnings', async () => {
    const consoleErrorSpy = vi
      .spyOn(console, 'error')
      .mockImplementation(() => {});

    writingStyleService.getUserWritingSamples.mockResolvedValue({
      samples: [
        {
          id: 'sample-1',
          title: 'Brand Voice',
          description: 'Friendly and concise style',
          word_count: 420,
          updated_at: '2026-03-08T00:00:00Z',
          preview: 'Test preview',
        },
      ],
    });
    writingStyleService.getActiveWritingSample.mockResolvedValue({
      sample: {
        id: 'sample-1',
      },
    });

    writingStyleService.uploadWritingSample.mockResolvedValue({});
    writingStyleService.deleteWritingSample.mockResolvedValue({});
    writingStyleService.setActiveWritingSample.mockResolvedValue({});
    writingStyleService.updateWritingSample.mockResolvedValue({});

    render(<WritingStyleManager />);

    await waitFor(() => {
      expect(screen.getByText('Brand Voice')).toBeInTheDocument();
    });

    const nestingWarnings = consoleErrorSpy.mock.calls.filter((call) =>
      String(call[0] || '').includes('validateDOMNesting')
    );
    expect(nestingWarnings).toHaveLength(0);

    consoleErrorSpy.mockRestore();
  });
});
