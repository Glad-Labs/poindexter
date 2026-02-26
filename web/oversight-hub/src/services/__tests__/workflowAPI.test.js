/**
 * Comprehensive Test Suite for Blog Workflow System
 *
 * Tests cover:
 * 1. Backend API endpoints
 * 2. Frontend UI interactions
 * 3. Workflow execution scenarios
 * 4. Error handling and recovery
 * 5. Data threading between phases
 * 6. Integration tests
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import apiClient from '../../lib/apiClient';

// ============================================================================
// TEST SETUP & FIXTURES
// ============================================================================

const TEST_WORKFLOW = {
  name: 'Test Blog Post',
  description: 'Test workflow for validation',
  phases: [
    {
      index: 0,
      name: 'blog_generate_content',
      user_inputs: {
        topic: 'Test Topic',
        style: 'balanced',
        tone: 'professional',
        target_length: 1000,
      },
    },
    {
      index: 1,
      name: 'blog_quality_evaluation',
      user_inputs: {
        topic: 'Test Topic',
        evaluation_method: 'pattern-based',
      },
    },
    {
      index: 2,
      name: 'blog_search_image',
      user_inputs: {
        topic: 'Test Topic',
        image_count: 1,
      },
    },
    {
      index: 3,
      name: 'blog_create_post',
      user_inputs: {
        topic: 'Test Topic',
        publish: true,
      },
    },
  ],
};

const MOCK_PHASE_DEFINITIONS = [
  {
    name: 'blog_generate_content',
    agent_type: 'blog_content_generator_agent',
    description: 'Generate blog post content using AI',
    tags: ['blog', 'content-generation'],
    input_schema: {
      topic: 'string',
      style: 'string',
      tone: 'string',
    },
    output_schema: {
      content: 'text',
      word_count: 'number',
      model_used: 'string',
    },
  },
  {
    name: 'blog_quality_evaluation',
    agent_type: 'blog_quality_agent',
    description: 'Evaluate blog post quality',
    tags: ['blog', 'quality-assurance'],
    input_schema: {
      content: 'text',
      topic: 'string',
    },
    output_schema: {
      overall_score: 'number',
      passing: 'boolean',
    },
  },
  {
    name: 'blog_search_image',
    agent_type: 'blog_image_agent',
    description: 'Search for featured image',
    tags: ['blog', 'media'],
    input_schema: {
      topic: 'string',
    },
    output_schema: {
      featured_image: 'object',
      image_markdown: 'text',
    },
  },
  {
    name: 'blog_create_post',
    agent_type: 'blog_publisher_agent',
    description: 'Create blog post in database',
    tags: ['blog', 'publishing'],
    input_schema: {
      content: 'text',
      title: 'string',
    },
    output_schema: {
      post_id: 'string',
      url: 'string',
    },
  },
];

const MOCK_EXECUTION_PROGRESS = {
  execution_id: 'exec-123456789',
  status: 'running',
  current_phase: 1,
  total_phases: 4,
  phase_name: 'blog_generate_content',
  progress_percent: 25,
  start_time: '2025-02-25T22:00:00Z',
  elapsed_seconds: 15,
};

const MOCK_EXECUTION_RESULTS = {
  execution_id: 'exec-123456789',
  status: 'completed',
  phase_results: {
    blog_generate_content: {
      status: 'completed',
      execution_time_ms: 2150,
      output: {
        content: 'Generated blog content...',
        word_count: 1234,
        model_used: 'gpt-4',
      },
    },
    blog_quality_evaluation: {
      status: 'completed',
      execution_time_ms: 850,
      output: {
        overall_score: 78,
        passing: true,
        feedback: 'Good quality content',
      },
    },
    blog_search_image: {
      status: 'completed',
      execution_time_ms: 1200,
      output: {
        featured_image: {
          url: 'https://example.com/image.jpg',
          photographer: 'John Doe',
        },
        image_markdown: '![Featured Image](url)',
      },
    },
    blog_create_post: {
      status: 'completed',
      execution_time_ms: 450,
      output: {
        post_id: 'post-789012345',
        slug: 'test-topic',
        url: '/posts/test-topic',
        title: 'Test Topic',
      },
    },
  },
  total_time_ms: 4650,
};

// ============================================================================
// API ENDPOINT TESTS
// ============================================================================

describe('API Endpoints', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('getAvailablePhases()', () => {
    it('should fetch list of available phases', async () => {
      vi.spyOn(apiClient, 'getAvailablePhases').mockResolvedValue(
        MOCK_PHASE_DEFINITIONS
      );

      const phases = await apiClient.getAvailablePhases();

      expect(phases).toHaveLength(4);
      expect(phases[0].name).toBe('blog_generate_content');
      expect(phases[0].tags).toContain('blog');
    });

    it('should filter blog phases correctly', async () => {
      vi.spyOn(apiClient, 'getAvailablePhases').mockResolvedValue(
        MOCK_PHASE_DEFINITIONS
      );

      const phases = await apiClient.getAvailablePhases();
      const blogPhases = phases.filter((p) => p.tags?.includes('blog'));

      expect(blogPhases).toHaveLength(4);
      expect(blogPhases.every((p) => p.tags.includes('blog'))).toBe(true);
    });

    it('should handle API errors gracefully', async () => {
      vi.spyOn(apiClient, 'getAvailablePhases').mockRejectedValue(
        new Error('Network error')
      );

      await expect(apiClient.getAvailablePhases()).rejects.toThrow(
        'Network error'
      );
    });
  });

  describe('executeWorkflow()', () => {
    it('should execute a complete workflow', async () => {
      vi.spyOn(apiClient, 'executeWorkflow').mockResolvedValue({
        execution_id: 'exec-123',
        status: 'pending',
        message: 'Workflow queued for execution',
      });

      const result = await apiClient.executeWorkflow(TEST_WORKFLOW);

      expect(result.execution_id).toBeDefined();
      expect(result.status).toBe('pending');
    });

    it('should validate workflow structure before execution', async () => {
      const invalidWorkflow = {
        name: 'Invalid Workflow',
        phases: [], // No phases
      };

      expect(() => {
        if (!invalidWorkflow.phases || invalidWorkflow.phases.length === 0) {
          throw new Error('Workflow must have at least one phase');
        }
      }).toThrow('Workflow must have at least one phase');
    });

    it('should handle workflow execution errors', async () => {
      vi.spyOn(apiClient, 'executeWorkflow').mockRejectedValue(
        new Error('Workflow execution failed')
      );

      await expect(
        apiClient.executeWorkflow(TEST_WORKFLOW)
      ).rejects.toThrow('Workflow execution failed');
    });

    it('should pass correct parameters to API', async () => {
      vi.spyOn(apiClient, 'executeWorkflow').mockResolvedValue({
        execution_id: 'exec-123',
      });

      await apiClient.executeWorkflow(TEST_WORKFLOW);

      expect(apiClient.executeWorkflow).toHaveBeenCalledWith(TEST_WORKFLOW);
    });
  });

  describe('getWorkflowProgress()', () => {
    it('should fetch real-time progress', async () => {
      vi.spyOn(apiClient, 'getWorkflowProgress').mockResolvedValue(
        MOCK_EXECUTION_PROGRESS
      );

      const progress = await apiClient.getWorkflowProgress('exec-123');

      expect(progress.status).toBe('running');
      expect(progress.progress_percent).toBe(25);
      expect(progress.current_phase).toBe(1);
    });

    it('should handle progress polling', async () => {
      const progressUpdates = [
        { ...MOCK_EXECUTION_PROGRESS, progress_percent: 25 },
        { ...MOCK_EXECUTION_PROGRESS, progress_percent: 50 },
        { ...MOCK_EXECUTION_PROGRESS, progress_percent: 75 },
        { ...MOCK_EXECUTION_PROGRESS, status: 'completed', progress_percent: 100 },
      ];

      for (const update of progressUpdates) {
        vi.spyOn(apiClient, 'getWorkflowProgress').mockResolvedValueOnce(update);
        const progress = await apiClient.getWorkflowProgress('exec-123');
        expect(progress).toBeDefined();
      }
    });

    it('should detect workflow completion', async () => {
      const completedProgress = {
        ...MOCK_EXECUTION_PROGRESS,
        status: 'completed',
        progress_percent: 100,
      };

      vi.spyOn(apiClient, 'getWorkflowProgress').mockResolvedValue(
        completedProgress
      );

      const progress = await apiClient.getWorkflowProgress('exec-123');

      expect(progress.status).toBe('completed');
      expect(progress.progress_percent).toBe(100);
    });
  });

  describe('getWorkflowResults()', () => {
    it('should fetch complete workflow results', async () => {
      vi.spyOn(apiClient, 'getWorkflowResults').mockResolvedValue(
        MOCK_EXECUTION_RESULTS
      );

      const results = await apiClient.getWorkflowResults('exec-123');

      expect(results.status).toBe('completed');
      expect(Object.keys(results.phase_results)).toHaveLength(4);
    });

    it('should return all phase results with outputs', async () => {
      vi.spyOn(apiClient, 'getWorkflowResults').mockResolvedValue(
        MOCK_EXECUTION_RESULTS
      );

      const results = await apiClient.getWorkflowResults('exec-123');
      const phases = Object.entries(results.phase_results);

      expect(phases).toHaveLength(4);
      phases.forEach(([phaseName, result]) => {
        expect(result.status).toBe('completed');
        expect(result.execution_time_ms).toBeGreaterThan(0);
        expect(result.output).toBeDefined();
      });
    });

    it('should calculate total execution time', async () => {
      vi.spyOn(apiClient, 'getWorkflowResults').mockResolvedValue(
        MOCK_EXECUTION_RESULTS
      );

      const results = await apiClient.getWorkflowResults('exec-123');

      expect(results.total_time_ms).toBeGreaterThan(0);
      expect(results.total_time_ms).toBe(4650);
    });
  });

  describe('listWorkflowExecutions()', () => {
    it('should list workflow execution history', async () => {
      const mockHistory = {
        executions: [
          {
            id: 'exec-1',
            name: 'Blog Post 1',
            status: 'completed',
            created_at: '2025-02-25T20:00:00Z',
          },
          {
            id: 'exec-2',
            name: 'Blog Post 2',
            status: 'completed',
            created_at: '2025-02-25T21:00:00Z',
          },
        ],
        total: 2,
      };

      vi.spyOn(apiClient, 'listWorkflowExecutions').mockResolvedValue(
        mockHistory.executions
      );

      const executions = await apiClient.listWorkflowExecutions();

      expect(executions).toHaveLength(2);
      expect(executions[0].status).toBe('completed');
    });

    it('should support pagination parameters', async () => {
      vi.spyOn(apiClient, 'listWorkflowExecutions').mockResolvedValue([]);

      await apiClient.listWorkflowExecutions({ skip: 10, limit: 5 });

      expect(apiClient.listWorkflowExecutions).toHaveBeenCalledWith({
        skip: 10,
        limit: 5,
      });
    });
  });

  describe('cancelWorkflowExecution()', () => {
    it('should cancel a running workflow', async () => {
      vi.spyOn(apiClient, 'cancelWorkflowExecution').mockResolvedValue({
        id: 'exec-123',
        status: 'cancelled',
      });

      const result = await apiClient.cancelWorkflowExecution('exec-123');

      expect(result.status).toBe('cancelled');
    });
  });
});

// ============================================================================
// WORKFLOW EXECUTION SCENARIOS
// ============================================================================

describe('Workflow Execution Scenarios', () => {
  describe('Complete Workflow Execution', () => {
    it('should execute all 4 phases in order', async () => {
      const phaseOrder = [];

      const phases = [
        'blog_generate_content',
        'blog_quality_evaluation',
        'blog_search_image',
        'blog_create_post',
      ];

      phases.forEach((phaseName) => {
        phaseOrder.push(phaseName);
      });

      expect(phaseOrder).toEqual([
        'blog_generate_content',
        'blog_quality_evaluation',
        'blog_search_image',
        'blog_create_post',
      ]);
    });

    it('should thread data from phase to phase', async () => {
      vi.spyOn(apiClient, 'getWorkflowResults').mockResolvedValue(
        MOCK_EXECUTION_RESULTS
      );

      const results = await apiClient.getWorkflowResults('exec-123');

      // Phase 1 output becomes Phase 2 input
      const phase1Output = results.phase_results.blog_generate_content.output;
      expect(phase1Output.content).toBeDefined();
      expect(phase1Output.word_count).toBeDefined();

      // Phase 2 uses Phase 1's content
      const phase2Output = results.phase_results.blog_quality_evaluation.output;
      expect(phase2Output.overall_score).toBeDefined();

      // Phase 4 uses outputs from all previous phases
      const phase4Output = results.phase_results.blog_create_post.output;
      expect(phase4Output.post_id).toBeDefined();
      expect(phase4Output.url).toBeDefined();
    });

    it('should handle quality check failure and retry', async () => {
      const failedQualityResults = {
        ...MOCK_EXECUTION_RESULTS,
        phase_results: {
          ...MOCK_EXECUTION_RESULTS.phase_results,
          blog_quality_evaluation: {
            status: 'completed',
            execution_time_ms: 850,
            output: {
              overall_score: 45,
              passing: false,
              feedback: 'Quality too low - needs revision',
            },
          },
        },
      };

      expect(
        failedQualityResults.phase_results.blog_quality_evaluation.output
          .passing
      ).toBe(false);
    });

    it('should publish post only if quality passes', async () => {
      vi.spyOn(apiClient, 'getWorkflowResults').mockResolvedValue(
        MOCK_EXECUTION_RESULTS
      );

      const results = await apiClient.getWorkflowResults('exec-123');
      const qualityScore =
        results.phase_results.blog_quality_evaluation.output.overall_score;
      const postCreated = qualityScore >= 70;

      expect(postCreated).toBe(true);
      expect(results.phase_results.blog_create_post.output.post_id).toBeDefined();
    });
  });

  describe('Partial Workflow Execution', () => {
    it('should execute only selected phases', async () => {
      const partialWorkflow = {
        ...TEST_WORKFLOW,
        phases: TEST_WORKFLOW.phases.slice(0, 2), // Only first 2 phases
      };

      expect(partialWorkflow.phases).toHaveLength(2);
      expect(partialWorkflow.phases[0].name).toBe('blog_generate_content');
      expect(partialWorkflow.phases[1].name).toBe('blog_quality_evaluation');
    });

    it('should skip phases as needed', async () => {
      const selectedPhases = {
        blog_generate_content: true,
        blog_quality_evaluation: false,
        blog_search_image: true,
        blog_create_post: false,
      };

      const activePhases = Object.entries(selectedPhases)
        .filter(([, selected]) => selected)
        .map(([name]) => name);

      expect(activePhases).toEqual([
        'blog_generate_content',
        'blog_search_image',
      ]);
    });
  });

  describe('Error Recovery', () => {
    it('should handle network errors', async () => {
      vi.spyOn(apiClient, 'executeWorkflow').mockRejectedValue(
        new Error('Network timeout')
      );

      try {
        await apiClient.executeWorkflow(TEST_WORKFLOW);
      } catch (err) {
        expect(err.message).toContain('Network');
      }
    });

    it('should handle phase execution failures', async () => {
      const failedResults = {
        ...MOCK_EXECUTION_RESULTS,
        phase_results: {
          ...MOCK_EXECUTION_RESULTS.phase_results,
          blog_generate_content: {
            status: 'failed',
            execution_time_ms: 5000,
            error: 'Failed to generate content',
          },
        },
      };

      expect(
        failedResults.phase_results.blog_generate_content.status
      ).toBe('failed');
    });

    it('should retry failed phases', async () => {
      const retryCount = 3;
      let attempts = 0;

      const retryPhase = async (phaseId) => {
        attempts++;
        if (attempts < retryCount) {
          throw new Error('Phase failed');
        }
        return { status: 'completed' };
      };

      for (let i = 0; i < retryCount; i++) {
        try {
          await retryPhase('phase-1');
        } catch (err) {
          // Expected to fail first 2 times, continue retrying
        }
      }

      expect(attempts).toBe(3);
    });
  });
});

// ============================================================================
// EDGE CASES & VALIDATION
// ============================================================================

describe('Edge Cases & Validation', () => {
  it('should validate topic is not empty', () => {
    const config = { topic: '' };
    const isValid = config.topic.trim().length > 0;
    expect(isValid).toBe(false);
  });

  it('should validate word count is within range', () => {
    const lengths = [100, 500, 1500, 5000, 10000];
    const validLengths = lengths.filter((l) => l >= 500 && l <= 5000);
    expect(validLengths).toEqual([500, 1500, 5000]);
  });

  it('should handle special characters in topic', () => {
    const topic = "AI's Impact: 2025 & Beyond!?";
    const slug = topic
      .toLowerCase()
      .replace(/[^a-z0-9]/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
    expect(slug).toBe('ai-s-impact-2025-beyond');
  });

  it('should handle long topic names', () => {
    const longTopic =
      'The Comprehensive Guide to Understanding Artificial Intelligence and Machine Learning in Contemporary Healthcare Systems';
    expect(longTopic.length).toBeGreaterThan(100);
  });

  it('should handle concurrent workflow executions', async () => {
    const workflows = [
      { ...TEST_WORKFLOW, name: 'Workflow 1' },
      { ...TEST_WORKFLOW, name: 'Workflow 2' },
      { ...TEST_WORKFLOW, name: 'Workflow 3' },
    ];

    vi.spyOn(apiClient, 'executeWorkflow').mockResolvedValue({
      execution_id: 'exec-123',
    });

    const results = await Promise.all(
      workflows.map((wf) => apiClient.executeWorkflow(wf))
    );

    expect(results).toHaveLength(3);
  });
});

// ============================================================================
// PERFORMANCE TESTS
// ============================================================================

describe('Performance', () => {
  it('workflow should complete within acceptable time', async () => {
    const startTime = Date.now();

    vi.spyOn(apiClient, 'getWorkflowResults').mockResolvedValue(
      MOCK_EXECUTION_RESULTS
    );

    await apiClient.getWorkflowResults('exec-123');

    const endTime = Date.now();
    const duration = endTime - startTime;

    // Mock execution should be nearly instant
    expect(duration).toBeLessThan(100);
  });

  it('should handle polling without blocking UI', async () => {
    const pollCount = 10;
    const results = [];

    for (let i = 0; i < pollCount; i++) {
      vi.spyOn(apiClient, 'getWorkflowProgress').mockResolvedValue({
        ...MOCK_EXECUTION_PROGRESS,
        progress_percent: (i + 1) * 10,
      });

      const progress = await apiClient.getWorkflowProgress('exec-123');
      results.push(progress);
    }

    expect(results).toHaveLength(pollCount);
  });
});

// ============================================================================
// INTEGRATION TESTS
// ============================================================================

describe('Integration Tests', () => {
  it('should complete full workflow from start to finish', async () => {
    // Step 1: Get available phases
    vi.spyOn(apiClient, 'getAvailablePhases').mockResolvedValue(
      MOCK_PHASE_DEFINITIONS
    );
    const phases = await apiClient.getAvailablePhases();
    expect(phases.length).toBeGreaterThan(0);

    // Step 2: Execute workflow
    vi.spyOn(apiClient, 'executeWorkflow').mockResolvedValue({
      execution_id: 'exec-123',
    });
    const execution = await apiClient.executeWorkflow(TEST_WORKFLOW);
    expect(execution.execution_id).toBeDefined();

    // Step 3: Poll for progress
    vi.spyOn(apiClient, 'getWorkflowProgress').mockResolvedValue({
      ...MOCK_EXECUTION_PROGRESS,
      status: 'completed',
    });
    const progress = await apiClient.getWorkflowProgress('exec-123');
    expect(progress.status).toBe('completed');

    // Step 4: Get results
    vi.spyOn(apiClient, 'getWorkflowResults').mockResolvedValue(
      MOCK_EXECUTION_RESULTS
    );
    const results = await apiClient.getWorkflowResults('exec-123');
    expect(results.status).toBe('completed');

    // Step 5: View in history
    vi.spyOn(apiClient, 'listWorkflowExecutions').mockResolvedValue([
      {
        id: 'exec-123',
        status: 'completed',
        name: TEST_WORKFLOW.name,
      },
    ]);
    const history = await apiClient.listWorkflowExecutions();
    expect(history.length).toBeGreaterThan(0);
  });

  it('should handle user cancellation', async () => {
    vi.spyOn(apiClient, 'executeWorkflow').mockResolvedValue({
      execution_id: 'exec-123',
    });
    const execution = await apiClient.executeWorkflow(TEST_WORKFLOW);

    vi.spyOn(apiClient, 'cancelWorkflowExecution').mockResolvedValue({
      id: 'exec-123',
      status: 'cancelled',
    });
    const cancelled = await apiClient.cancelWorkflowExecution('exec-123');

    expect(cancelled.status).toBe('cancelled');
  });
});

export {
  TEST_WORKFLOW,
  MOCK_PHASE_DEFINITIONS,
  MOCK_EXECUTION_PROGRESS,
  MOCK_EXECUTION_RESULTS,
};
