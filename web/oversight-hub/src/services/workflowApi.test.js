/**
 * Workflow API Client Tests
 *
 * Tests the REST API integration layer for workflows
 * Verifies: API calls, error handling, response parsing, authentication
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import workflowApi from '../services/workflowApi';

// Mock fetch
global.fetch = vi.fn();

describe('Workflow API Client', () => {
  const mockToken = 'test-auth-token-123';

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem('authToken', mockToken);
  });

  afterEach(() => {
    localStorage.clear();
  });

  describe('Execute Workflow', () => {
    it('should execute a workflow template', async () => {
      const mockResponse = {
        id: 'workflow-123',
        status: 'running',
        progress: 0,
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const result = await workflowApi.executeWorkflow('blog_post');

      expect(result.id).toBe('workflow-123');
      expect(result.status).toBe('running');
    });

    it('should pass authentication token', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 'test' }),
      });

      await workflowApi.executeWorkflow('blog_post');

      expect(global.fetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: `Bearer ${mockToken}`,
          }),
        })
      );
    });

    it('should include initial inputs in request', async () => {
      const inputs = { topic: 'AI', keywords: ['machine learning'] };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 'test' }),
      });

      await workflowApi.executeWorkflow('blog_post', inputs);

      expect(global.fetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: expect.stringContaining(JSON.stringify(inputs)),
        })
      );
    });

    it('should handle authentication errors', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ error: 'Unauthorized' }),
      });

      await expect(workflowApi.executeWorkflow('blog_post')).rejects.toThrow();
    });

    it('should handle network errors', async () => {
      global.fetch.mockRejectedValueOnce(new Error('Network error'));

      await expect(workflowApi.executeWorkflow('blog_post')).rejects.toThrow(
        'Network error'
      );
    });

    it('should handle server errors', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ error: 'Internal server error' }),
      });

      await expect(workflowApi.executeWorkflow('blog_post')).rejects.toThrow();
    });
  });

  describe('Get Workflow Status', () => {
    it('should retrieve workflow status', async () => {
      const mockResponse = {
        id: 'workflow-123',
        status: 'running',
        progress: 50,
        currentPhase: 'creative',
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const result = await workflowApi.getWorkflowStatus('workflow-123');

      expect(result.status).toBe('running');
      expect(result.progress).toBe(50);
      expect(result.currentPhase).toBe('creative');
    });

    it('should handle 404 for nonexistent workflow', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 404,
        json: async () => ({ error: 'Workflow not found' }),
      });

      await expect(
        workflowApi.getWorkflowStatus('nonexistent')
      ).rejects.toThrow();
    });
  });

  describe('Get Workflow Results', () => {
    it('should retrieve completed workflow results', async () => {
      const mockResponse = {
        id: 'workflow-123',
        status: 'completed',
        results: {
          content: 'Generated blog post...',
          images: ['image1.jpg', 'image2.jpg'],
        },
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const result = await workflowApi.getWorkflowResults('workflow-123');

      expect(result.status).toBe('completed');
      expect(result.results.content).toBeDefined();
      expect(result.results.images).toHaveLength(2);
    });

    it('should handle incomplete workflow results', async () => {
      const mockResponse = {
        id: 'workflow-123',
        status: 'running',
        results: null,
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const result = await workflowApi.getWorkflowResults('workflow-123');

      expect(result.results).toBeNull();
    });
  });

  describe('Cancel Workflow', () => {
    it('should cancel a running workflow', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 'workflow-123', status: 'cancelled' }),
      });

      const result = await workflowApi.cancelWorkflow('workflow-123');

      expect(result.status).toBe('cancelled');
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('workflow-123'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });

    it('should handle already completed workflow', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: async () => ({ error: 'Cannot cancel completed workflow' }),
      });

      await expect(
        workflowApi.cancelWorkflow('workflow-123')
      ).rejects.toThrow();
    });
  });

  describe('List Workflow Templates', () => {
    it('should retrieve available workflow templates', async () => {
      const mockResponse = [
        { name: 'blog_post', description: 'Generate blog posts' },
        { name: 'social_media', description: 'Social media content' },
        { name: 'email', description: 'Email campaigns' },
      ];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const result = await workflowApi.getWorkflowTemplates();

      expect(result).toHaveLength(3);
      expect(result[0].name).toBe('blog_post');
    });

    it('should handle empty template list', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });

      const result = await workflowApi.getWorkflowTemplates();

      expect(result).toEqual([]);
    });
  });

  describe('List User Workflows', () => {
    it('should retrieve user workflows with pagination', async () => {
      const mockResponse = {
        workflows: [
          { id: 'w1', name: 'Blog Post', status: 'completed' },
          { id: 'w2', name: 'Social Post', status: 'running' },
        ],
        total: 2,
        page: 1,
        pageSize: 10,
      };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const result = await workflowApi.getUserWorkflows(1, 10);

      expect(result.workflows).toHaveLength(2);
      expect(result.total).toBe(2);
    });

    it('should support filtering by status', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ workflows: [], total: 0 }),
      });

      await workflowApi.getUserWorkflows(1, 10, 'completed');

      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('status=completed'),
        expect.any(Object)
      );
    });
  });

  describe('Update Workflow', () => {
    it('should update workflow parameters', async () => {
      const updates = { model: 'gpt-4-turbo', priority: 'high' };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 'workflow-123', ...updates }),
      });

      const result = await workflowApi.updateWorkflow('workflow-123', updates);

      expect(result.model).toBe('gpt-4-turbo');
      expect(result.priority).toBe('high');
    });

    it('should handle validation errors', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: async () => ({ error: 'Invalid model' }),
      });

      await expect(
        workflowApi.updateWorkflow('workflow-123', { model: 'invalid' })
      ).rejects.toThrow();
    });
  });

  describe('Error Handling', () => {
    it('should retry failed requests', async () => {
      // First call fails, second succeeds
      global.fetch
        .mockResolvedValueOnce({
          ok: false,
          status: 503,
          json: async () => ({ error: 'Service unavailable' }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ id: 'workflow-123' }),
        });

      const result = await workflowApi.executeWorkflow('blog_post');

      expect(result.id).toBe('workflow-123');
      expect(global.fetch).toHaveBeenCalledTimes(2);
    });

    it('should timeout after max retries', async () => {
      global.fetch.mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({ error: 'Service unavailable' }),
      });

      await expect(
        workflowApi.executeWorkflow('blog_post', {}, { maxRetries: 1 })
      ).rejects.toThrow();
    });
  });

  describe('Content-Type Headers', () => {
    it('should set correct Content-Type for JSON requests', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 'test' }),
      });

      await workflowApi.executeWorkflow('blog_post');

      expect(global.fetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
        })
      );
    });
  });
});
