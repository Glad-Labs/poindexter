/**
 * orchestratorAdapter.test.js
 *
 * Unit tests for services/orchestratorAdapter.js.
 *
 * Tests cover:
 * - getExecutions — success (maps to legacy format), network error returns fallback
 * - getStats — success (aggregates correctly), network error returns fallback with zeroes
 * - processRequest — success (maps to legacy format), error returns fallback
 * - getOverallStatus — success, error returns fallback
 * - getTrainingMetrics — success (groups by type), error returns fallback
 * - getTrainingData — success, error returns fallback
 * - requestApproval — success, error returns fallback
 * - submitApproval — success with decision, error returns fallback
 * - getExecutionTools — success (maps agent registry), error returns fallback
 * - healthCheck — healthy phase4, unhealthy phase4, error
 *
 * phase4Client and errorLoggingService are fully mocked; no network calls.
 */

import { vi } from 'vitest';

const { mockPhase4Client, mockLogError } = vi.hoisted(() => ({
  mockPhase4Client: {
    taskClient: {
      listTasks: vi.fn(),
      approveTask: vi.fn(),
    },
    workflowClient: {
      getTemplates: vi.fn(),
      executeWorkflow: vi.fn(),
    },
    agentDiscoveryClient: {
      getRegistry: vi.fn(),
    },
    serviceRegistryClient: {
      listServices: vi.fn(),
    },
    healthCheck: vi.fn(),
  },
  mockLogError: vi.fn(),
}));

vi.mock('../phase4Client', () => ({
  default: mockPhase4Client,
}));

vi.mock('../errorLoggingService', () => ({
  logError: mockLogError,
}));

import orchestratorAdapter from '../orchestratorAdapter';

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getExecutions
// ---------------------------------------------------------------------------

describe('getExecutions', () => {
  it('maps tasks to legacy executions format', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([
      {
        id: 'task-1',
        status: 'completed',
        input: { topic: 'AI' },
        output: { content: 'Article' },
        created_at: '2026-01-01T00:00:00Z',
        completed_at: '2026-01-01T01:00:00Z',
        duration: 3600,
        phase: 'creative',
        assigned_agent: 'content_agent',
      },
    ]);
    const result = await orchestratorAdapter.getExecutions();
    expect(result.total).toBe(1);
    expect(result.executions[0]).toMatchObject({
      id: 'task-1',
      status: 'completed',
      phase: 'creative',
      agent: 'content_agent',
    });
  });

  it('passes type=orchestrator filter to taskClient', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([]);
    await orchestratorAdapter.getExecutions({ status: 'pending', limit: 10 });
    expect(mockPhase4Client.taskClient.listTasks).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'orchestrator', status: 'pending' }),
      10
    );
  });

  it('returns fallback with empty executions on error', async () => {
    mockPhase4Client.taskClient.listTasks.mockRejectedValue(
      new Error('Network error')
    );
    const result = await orchestratorAdapter.getExecutions();
    expect(result.executions).toEqual([]);
    expect(result.total).toBe(0);
    expect(result.error).toBeDefined();
  });

  it('includes timestamp in result', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([]);
    const result = await orchestratorAdapter.getExecutions();
    expect(result.timestamp).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// getStats
// ---------------------------------------------------------------------------

describe('getStats', () => {
  beforeEach(() => {
    mockPhase4Client.workflowClient.getTemplates.mockResolvedValue([
      { id: 'tpl-1' },
      { id: 'tpl-2' },
    ]);
  });

  it('calculates aggregated stats from tasks', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([
      { status: 'completed', phase: 'creative' },
      { status: 'completed', phase: 'research' },
      { status: 'failed', phase: 'creative' },
      { status: 'pending', phase: 'research' },
    ]);
    const result = await orchestratorAdapter.getStats();
    expect(result.totalExecutions).toBe(4);
    expect(result.completed).toBe(2);
    expect(result.failed).toBe(1);
    expect(result.pending).toBe(1);
    expect(result.activeWorkflows).toBe(2);
  });

  it('calculates successRate correctly', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([
      { status: 'completed', phase: 'creative' },
      { status: 'completed', phase: 'research' },
      { status: 'failed', phase: 'creative' },
      { status: 'failed', phase: 'qa' },
    ]);
    const result = await orchestratorAdapter.getStats();
    expect(result.successRate).toBe(50);
  });

  it('groups tasks by phase', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([
      { status: 'completed', phase: 'creative' },
      { status: 'failed', phase: 'creative' },
      { status: 'completed', phase: 'research' },
    ]);
    const result = await orchestratorAdapter.getStats();
    expect(result.phaseStats.creative.total).toBe(2);
    expect(result.phaseStats.creative.completed).toBe(1);
    expect(result.phaseStats.research.total).toBe(1);
  });

  it('returns fallback zeroes on error', async () => {
    mockPhase4Client.workflowClient.getTemplates.mockRejectedValue(
      new Error('Service down')
    );
    const result = await orchestratorAdapter.getStats();
    expect(result.totalExecutions).toBe(0);
    expect(result.successRate).toBe(0);
    expect(result.error).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// processRequest
// ---------------------------------------------------------------------------

describe('processRequest', () => {
  it('maps executeWorkflow response to legacy format', async () => {
    mockPhase4Client.workflowClient.executeWorkflow.mockResolvedValue({
      id: 'exec-123',
      status: 'RUNNING',
    });
    const result = await orchestratorAdapter.processRequest('blog-template', {
      topic: 'AI',
    });
    expect(result.executionId).toBe('exec-123');
    expect(result.status).toBe('RUNNING'); // spread overwrites 'processing' with actual status
    expect(result.template).toBe('blog-template');
  });

  it('uses execution_id when id is absent', async () => {
    mockPhase4Client.workflowClient.executeWorkflow.mockResolvedValue({
      execution_id: 'exec-456',
    });
    const result = await orchestratorAdapter.processRequest('template-x');
    expect(result.executionId).toBe('exec-456');
  });

  it('returns fallback with failed status on error', async () => {
    mockPhase4Client.workflowClient.executeWorkflow.mockRejectedValue(
      new Error('Timeout')
    );
    const result = await orchestratorAdapter.processRequest('template-x');
    expect(result.status).toBe('failed');
    expect(result.error).toBe('Timeout');
  });
});

// ---------------------------------------------------------------------------
// getOverallStatus
// ---------------------------------------------------------------------------

describe('getOverallStatus', () => {
  it('returns operational status with counts', async () => {
    mockPhase4Client.agentDiscoveryClient.getRegistry.mockResolvedValue({
      agent1: { name: 'Content Agent' },
      agent2: { name: 'Market Agent' },
    });
    mockPhase4Client.serviceRegistryClient.listServices.mockResolvedValue([
      'svc1',
      'svc2',
      'svc3',
    ]);
    mockPhase4Client.workflowClient.getTemplates.mockResolvedValue([]);
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([]);
    const result = await orchestratorAdapter.getOverallStatus();
    expect(result.status).toBe('operational');
    expect(result.agentCount).toBe(2);
    expect(result.serviceCount).toBe(3);
  });

  it('handles services as object (not array)', async () => {
    mockPhase4Client.agentDiscoveryClient.getRegistry.mockResolvedValue({});
    mockPhase4Client.serviceRegistryClient.listServices.mockResolvedValue({
      svc1: {},
      svc2: {},
    });
    mockPhase4Client.workflowClient.getTemplates.mockResolvedValue([]);
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([]);
    const result = await orchestratorAdapter.getOverallStatus();
    expect(result.serviceCount).toBe(2);
  });

  it('returns error status on failure', async () => {
    mockPhase4Client.agentDiscoveryClient.getRegistry.mockRejectedValue(
      new Error('Registry down')
    );
    const result = await orchestratorAdapter.getOverallStatus();
    expect(result.status).toBe('error');
    expect(result.error).toBe('Registry down');
  });
});

// ---------------------------------------------------------------------------
// getTrainingMetrics
// ---------------------------------------------------------------------------

describe('getTrainingMetrics', () => {
  it('returns training metrics grouped by subtype', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([
      { status: 'completed', subtype: 'blog' },
      { status: 'completed', subtype: 'blog' },
      { status: 'completed', subtype: 'email' },
    ]);
    const result = await orchestratorAdapter.getTrainingMetrics();
    expect(result.totalRecords).toBe(3);
    expect(result.byType.blog).toHaveLength(2);
    expect(result.byType.email).toHaveLength(1);
  });

  it('uses "general" subtype when task.subtype is absent', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([
      { status: 'completed' }, // no subtype
    ]);
    const result = await orchestratorAdapter.getTrainingMetrics();
    expect(result.byType.general).toHaveLength(1);
  });

  it('passes type=training filter to taskClient', async () => {
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([]);
    await orchestratorAdapter.getTrainingMetrics();
    expect(mockPhase4Client.taskClient.listTasks).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'training' }),
      expect.any(Number)
    );
  });

  it('returns fallback on error', async () => {
    mockPhase4Client.taskClient.listTasks.mockRejectedValue(
      new Error('Timeout')
    );
    const result = await orchestratorAdapter.getTrainingMetrics();
    expect(result.totalRecords).toBe(0);
    expect(result.byType).toEqual({});
    expect(result.error).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// getTrainingData
// ---------------------------------------------------------------------------

describe('getTrainingData', () => {
  it('returns training data for specific type', async () => {
    const tasks = [
      { id: 't1', subtype: 'blog' },
      { id: 't2', subtype: 'blog' },
    ];
    mockPhase4Client.taskClient.listTasks.mockResolvedValue(tasks);
    const result = await orchestratorAdapter.getTrainingData('blog');
    expect(result.type).toBe('blog');
    expect(result.count).toBe(2);
    expect(result.data).toEqual(tasks);
  });

  it('returns fallback on error', async () => {
    mockPhase4Client.taskClient.listTasks.mockRejectedValue(
      new Error('Network down')
    );
    const result = await orchestratorAdapter.getTrainingData('email');
    expect(result.type).toBe('email');
    expect(result.data).toEqual([]);
    expect(result.count).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// requestApproval
// ---------------------------------------------------------------------------

describe('requestApproval', () => {
  it('returns approval request result in legacy format', async () => {
    // Note: result spreads the approveTask response, so result.status comes from
    // the spread of approveTask return value (which overwrites 'approval_requested')
    mockPhase4Client.taskClient.approveTask.mockResolvedValue({});
    const result = await orchestratorAdapter.requestApproval('exec-1', {
      priority: 'high',
    });
    expect(result.executionId).toBe('exec-1');
    // When approveTask returns {} (no status), status falls back to 'approval_requested'
    expect(result.status).toBe('approval_requested');
  });

  it('sends action:request_approval with extra data', async () => {
    mockPhase4Client.taskClient.approveTask.mockResolvedValue({});
    await orchestratorAdapter.requestApproval('exec-5', { notes: 'Urgent' });
    expect(mockPhase4Client.taskClient.approveTask).toHaveBeenCalledWith(
      'exec-5',
      expect.objectContaining({ action: 'request_approval', notes: 'Urgent' })
    );
  });

  it('returns fallback on error', async () => {
    mockPhase4Client.taskClient.approveTask.mockRejectedValue(
      new Error('Permission denied')
    );
    const result = await orchestratorAdapter.requestApproval('exec-1');
    expect(result.status).toBe('error');
    expect(result.error).toBe('Permission denied');
  });
});

// ---------------------------------------------------------------------------
// submitApproval
// ---------------------------------------------------------------------------

describe('submitApproval', () => {
  it('returns approval submission result with decision', async () => {
    mockPhase4Client.taskClient.approveTask.mockResolvedValue({ id: 'exec-1' });
    const result = await orchestratorAdapter.submitApproval(
      'exec-1',
      'approved',
      'LGTM'
    );
    expect(result.executionId).toBe('exec-1');
    expect(result.status).toBe('approval_submitted');
    expect(result.decision).toBe('approved');
  });

  it('passes decision and notes to approveTask', async () => {
    mockPhase4Client.taskClient.approveTask.mockResolvedValue({});
    await orchestratorAdapter.submitApproval(
      'exec-7',
      'rejected',
      'Low quality'
    );
    expect(mockPhase4Client.taskClient.approveTask).toHaveBeenCalledWith(
      'exec-7',
      expect.objectContaining({ decision: 'rejected', notes: 'Low quality' })
    );
  });

  it('returns fallback on error', async () => {
    mockPhase4Client.taskClient.approveTask.mockRejectedValue(
      new Error('Task not found')
    );
    const result = await orchestratorAdapter.submitApproval(
      'exec-1',
      'approved'
    );
    expect(result.status).toBe('error');
    expect(result.error).toBe('Task not found');
  });
});

// ---------------------------------------------------------------------------
// getExecutionTools
// ---------------------------------------------------------------------------

describe('getExecutionTools', () => {
  it('maps agent registry to tools array', async () => {
    mockPhase4Client.agentDiscoveryClient.getRegistry.mockResolvedValue({
      content_agent: {
        name: 'Content Agent',
        capabilities: ['blog', 'email'],
        phases: ['creative'],
        description: 'Creates content',
      },
    });
    const result = await orchestratorAdapter.getExecutionTools();
    expect(result.count).toBe(1);
    expect(result.tools[0]).toMatchObject({
      id: 'content_agent',
      name: 'Content Agent',
      capabilities: ['blog', 'email'],
    });
  });

  it('handles agents with missing optional fields', async () => {
    mockPhase4Client.agentDiscoveryClient.getRegistry.mockResolvedValue({
      bare_agent: {},
    });
    const result = await orchestratorAdapter.getExecutionTools();
    expect(result.tools[0].capabilities).toEqual([]);
    expect(result.tools[0].description).toBe('');
  });

  it('returns fallback with empty tools on error', async () => {
    mockPhase4Client.agentDiscoveryClient.getRegistry.mockRejectedValue(
      new Error('Registry down')
    );
    const result = await orchestratorAdapter.getExecutionTools();
    expect(result.tools).toEqual([]);
    expect(result.count).toBe(0);
    expect(result.error).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// healthCheck
// ---------------------------------------------------------------------------

describe('healthCheck', () => {
  it('returns healthy:true when phase4 is healthy and stats succeed', async () => {
    mockPhase4Client.healthCheck.mockResolvedValue({ healthy: true });
    mockPhase4Client.workflowClient.getTemplates.mockResolvedValue([]);
    mockPhase4Client.taskClient.listTasks.mockResolvedValue([]);
    const result = await orchestratorAdapter.healthCheck();
    expect(result.healthy).toBe(true);
  });

  it('returns healthy:false when phase4 is not healthy', async () => {
    mockPhase4Client.healthCheck.mockResolvedValue({
      healthy: false,
      message: 'DB down',
    });
    const result = await orchestratorAdapter.healthCheck();
    expect(result.healthy).toBe(false);
    expect(result.message).toBe('Phase 4 client not accessible');
  });

  it('returns healthy:false on thrown error', async () => {
    mockPhase4Client.healthCheck.mockRejectedValue(
      new Error('Connection refused')
    );
    const result = await orchestratorAdapter.healthCheck();
    expect(result.healthy).toBe(false);
    expect(result.error).toBe('Connection refused');
  });
});
