/**
 * writingStyleService.test.js
 *
 * Unit tests for services/writingStyleService.js.
 *
 * Tests cover:
 * - uploadWritingSample — string content, File content, setAsActive flag
 * - getUserWritingSamples — calls correct endpoint
 * - getActiveWritingSample — calls correct endpoint
 * - setActiveWritingSample — calls correct endpoint with sample ID
 * - updateWritingSample — calls correct endpoint with updates
 * - deleteWritingSample — calls correct endpoint with sample ID
 *
 * makeRequest is mocked; no network calls.
 */

import { vi } from 'vitest';

const { mockMakeRequest } = vi.hoisted(() => ({
  mockMakeRequest: vi.fn(),
}));

vi.mock('@/services/cofounderAgentClient', () => ({
  makeRequest: mockMakeRequest,
}));

import {
  uploadWritingSample,
  getUserWritingSamples,
  getActiveWritingSample,
  setActiveWritingSample,
  updateWritingSample,
  deleteWritingSample,
} from '../writingStyleService';

beforeEach(() => {
  vi.clearAllMocks();
  mockMakeRequest.mockResolvedValue({ id: 'sample-1' });
});

// ---------------------------------------------------------------------------
// uploadWritingSample
// ---------------------------------------------------------------------------

describe('uploadWritingSample', () => {
  it('calls upload endpoint via POST', async () => {
    await uploadWritingSample('My Title', 'My Description', 'Sample content');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/writing-style/upload');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });

  it('passes FormData for string content', async () => {
    await uploadWritingSample('Title', 'Desc', 'Content text');
    const formData = mockMakeRequest.mock.calls[0][2];
    expect(formData).toBeInstanceOf(FormData);
    expect(formData.get('title')).toBe('Title');
    expect(formData.get('content')).toBe('Content text');
  });

  it('appends file when content is a File object', async () => {
    const file = new File(['file content'], 'sample.txt', {
      type: 'text/plain',
    });
    await uploadWritingSample('Title', 'Desc', file);
    const formData = mockMakeRequest.mock.calls[0][2];
    expect(formData.get('file')).toBe(file);
    expect(formData.get('content')).toBeNull();
  });

  it('uses empty string for description when null', async () => {
    await uploadWritingSample('Title', null, 'Content');
    const formData = mockMakeRequest.mock.calls[0][2];
    expect(formData.get('description')).toBe('');
  });

  it('passes setAsActive flag to form data', async () => {
    await uploadWritingSample('Title', 'Desc', 'Content', true);
    const formData = mockMakeRequest.mock.calls[0][2];
    // FormData stores everything as strings
    expect(formData.get('set_as_active')).toBeTruthy();
  });

  it('returns response from makeRequest', async () => {
    mockMakeRequest.mockResolvedValue({ id: 'new-sample-id' });
    const result = await uploadWritingSample('Title', 'Desc', 'Content');
    expect(result.id).toBe('new-sample-id');
  });
});

// ---------------------------------------------------------------------------
// getUserWritingSamples
// ---------------------------------------------------------------------------

describe('getUserWritingSamples', () => {
  it('calls samples endpoint via GET', async () => {
    await getUserWritingSamples();
    expect(mockMakeRequest).toHaveBeenCalledWith(
      '/api/writing-style/samples',
      'GET'
    );
  });

  it('returns response from makeRequest', async () => {
    mockMakeRequest.mockResolvedValue({ samples: [] });
    const result = await getUserWritingSamples();
    expect(result.samples).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// getActiveWritingSample
// ---------------------------------------------------------------------------

describe('getActiveWritingSample', () => {
  it('calls active endpoint via GET', async () => {
    await getActiveWritingSample();
    expect(mockMakeRequest).toHaveBeenCalledWith(
      '/api/writing-style/active',
      'GET'
    );
  });
});

// ---------------------------------------------------------------------------
// setActiveWritingSample
// ---------------------------------------------------------------------------

describe('setActiveWritingSample', () => {
  it('calls activate endpoint for the given sample ID via POST', async () => {
    await setActiveWritingSample('sample-42');
    expect(mockMakeRequest).toHaveBeenCalledWith(
      '/api/writing-style/sample-42/activate',
      'POST'
    );
  });
});

// ---------------------------------------------------------------------------
// updateWritingSample
// ---------------------------------------------------------------------------

describe('updateWritingSample', () => {
  it('calls sample ID endpoint via PUT with updates', async () => {
    const updates = { title: 'New Title', content: 'New content' };
    await updateWritingSample('sample-7', updates);
    expect(mockMakeRequest).toHaveBeenCalledWith(
      '/api/writing-style/sample-7',
      'PUT',
      updates
    );
  });
});

// ---------------------------------------------------------------------------
// deleteWritingSample
// ---------------------------------------------------------------------------

describe('deleteWritingSample', () => {
  it('calls sample ID endpoint via DELETE', async () => {
    await deleteWritingSample('sample-99');
    expect(mockMakeRequest).toHaveBeenCalledWith(
      '/api/writing-style/sample-99',
      'DELETE'
    );
  });
});
