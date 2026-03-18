/**
 * useWorkflowStudio.test.js
 *
 * Unit tests for the useWorkflowStudio hook.
 *
 * Covers:
 * - Initial state
 * - loadWorkflowStudioData: fetches phases, workflows, sets templates
 * - loadWorkflowStudioData: error path calls onError
 * - handleDeleteWorkflow: confirm + delete + removes from local state
 * - handleDeleteWorkflow: cancel does nothing
 * - handleExecuteWorkflow: success + error
 * - handleWorkflowSaved: appends + switches tab
 * - handleStudioTabChange, handleSelectWorkflowForEdit
 * - handleOpenTemplateModal / handleCloseTemplateModal
 * - Return shape
 *
 * Closes #919 (partial).
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import useWorkflowStudio from '../useWorkflowStudio';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const {
  mockGetAvailablePhases,
  mockListWorkflows,
  mockDeleteWorkflow,
  mockExecuteWorkflowMgmt,
} = vi.hoisted(() => ({
  mockGetAvailablePhases: vi.fn(),
  mockListWorkflows: vi.fn(),
  mockDeleteWorkflow: vi.fn(),
  mockExecuteWorkflowMgmt: vi.fn(),
}));

vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../services/workflowBuilderService', () => ({
  getAvailablePhases: (...args) => mockGetAvailablePhases(...args),
  listWorkflows: (...args) => mockListWorkflows(...args),
  deleteWorkflow: (...args) => mockDeleteWorkflow(...args),
}));

vi.mock('../../services/workflowManagementService', () => ({
  executeWorkflow: (...args) => mockExecuteWorkflowMgmt(...args),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SAMPLE_PHASES = [
  { id: 'research', name: 'Research' },
  { id: 'draft', name: 'Draft' },
];

const SAMPLE_WORKFLOWS = [
  { id: 'wf-1', name: 'Blog Pipeline' },
  { id: 'wf-2', name: 'Social Pipeline' },
];

function setupSuccessMocks() {
  mockGetAvailablePhases.mockResolvedValue({ phases: SAMPLE_PHASES });
  mockListWorkflows.mockResolvedValue({ workflows: SAMPLE_WORKFLOWS });
  mockDeleteWorkflow.mockResolvedValue({});
  mockExecuteWorkflowMgmt.mockResolvedValue({});
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useWorkflowStudio', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupSuccessMocks();
    // Mock window.confirm
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial state ------------------------------------------------------

  it('has correct initial state', () => {
    const { result } = renderHook(() => useWorkflowStudio());

    expect(result.current.studioTab).toBe(0);
    expect(result.current.availablePhases).toEqual([]);
    expect(result.current.workflows).toEqual([]);
    expect(result.current.templates).toEqual([]);
    expect(result.current.loadingWorkflows).toBe(false);
    expect(result.current.selectedWorkflow).toBeNull();
    expect(result.current.selectedTemplate).toBeNull();
    expect(result.current.templateModalOpen).toBe(false);
  });

  // ---- loadWorkflowStudioData ---------------------------------------------

  it('loads phases, workflows, and templates on success', async () => {
    const { result } = renderHook(() => useWorkflowStudio());

    await act(async () => {
      await result.current.loadWorkflowStudioData();
    });

    expect(result.current.availablePhases).toEqual(SAMPLE_PHASES);
    expect(result.current.workflows).toEqual(SAMPLE_WORKFLOWS);
    expect(result.current.templates).toHaveLength(3); // DEFAULT_TEMPLATES
    expect(result.current.loadingWorkflows).toBe(false);
  });

  it('calls onError on API failure', async () => {
    mockGetAvailablePhases.mockRejectedValue(new Error('Phases failed'));
    const onError = vi.fn();

    const { result } = renderHook(() => useWorkflowStudio({ onError }));

    await act(async () => {
      await result.current.loadWorkflowStudioData();
    });

    expect(onError).toHaveBeenCalledWith(
      expect.stringContaining('Workflow Error')
    );
    expect(result.current.loadingWorkflows).toBe(false);
  });

  // ---- handleDeleteWorkflow -----------------------------------------------

  it('deletes workflow after confirmation', async () => {
    const { result } = renderHook(() => useWorkflowStudio());

    // Load workflows first
    await act(async () => {
      await result.current.loadWorkflowStudioData();
    });

    expect(result.current.workflows).toHaveLength(2);

    await act(async () => {
      await result.current.handleDeleteWorkflow('wf-1');
    });

    expect(mockDeleteWorkflow).toHaveBeenCalledWith('wf-1');
    expect(result.current.workflows).toHaveLength(1);
    expect(result.current.workflows[0].id).toBe('wf-2');
  });

  it('does nothing when user cancels confirmation', async () => {
    window.confirm.mockReturnValue(false);

    const { result } = renderHook(() => useWorkflowStudio());

    await act(async () => {
      await result.current.loadWorkflowStudioData();
    });

    await act(async () => {
      await result.current.handleDeleteWorkflow('wf-1');
    });

    expect(mockDeleteWorkflow).not.toHaveBeenCalled();
    expect(result.current.workflows).toHaveLength(2);
  });

  it('calls onError when delete fails', async () => {
    mockDeleteWorkflow.mockRejectedValue(new Error('Delete denied'));
    const onError = vi.fn();

    const { result } = renderHook(() => useWorkflowStudio({ onError }));

    await act(async () => {
      await result.current.loadWorkflowStudioData();
    });

    await act(async () => {
      await result.current.handleDeleteWorkflow('wf-1');
    });

    expect(onError).toHaveBeenCalledWith('Delete denied');
  });

  // ---- handleExecuteWorkflow ----------------------------------------------

  it('executes workflow successfully', async () => {
    const { result } = renderHook(() => useWorkflowStudio());

    await act(async () => {
      await result.current.handleExecuteWorkflow('wf-1');
    });

    expect(mockExecuteWorkflowMgmt).toHaveBeenCalledWith('wf-1');
  });

  it('calls onError when execution fails', async () => {
    mockExecuteWorkflowMgmt.mockRejectedValue(new Error('Exec error'));
    const onError = vi.fn();

    const { result } = renderHook(() => useWorkflowStudio({ onError }));

    await act(async () => {
      await result.current.handleExecuteWorkflow('wf-1');
    });

    expect(onError).toHaveBeenCalledWith('Exec error');
  });

  // ---- handleWorkflowSaved ------------------------------------------------

  it('appends new workflow and switches to My Workflows tab', async () => {
    const { result } = renderHook(() => useWorkflowStudio());

    await act(async () => {
      await result.current.loadWorkflowStudioData();
    });

    act(() =>
      result.current.handleWorkflowSaved({ id: 'wf-new', name: 'New Flow' })
    );

    expect(result.current.workflows).toHaveLength(3);
    expect(result.current.workflows[2].id).toBe('wf-new');
    expect(result.current.studioTab).toBe(1);
  });

  // ---- Tab / selection handlers -------------------------------------------

  it('handleStudioTabChange updates tab', () => {
    const { result } = renderHook(() => useWorkflowStudio());

    act(() => result.current.handleStudioTabChange(null, 2));

    expect(result.current.studioTab).toBe(2);
  });

  it('handleSelectWorkflowForEdit sets workflow and switches to tab 0', async () => {
    const { result } = renderHook(() => useWorkflowStudio());

    act(() => result.current.handleStudioTabChange(null, 1));
    expect(result.current.studioTab).toBe(1);

    const wf = { id: 'wf-edit', name: 'Edit Me' };
    act(() => result.current.handleSelectWorkflowForEdit(wf));

    expect(result.current.selectedWorkflow).toEqual(wf);
    expect(result.current.studioTab).toBe(0);
  });

  // ---- Template modal -----------------------------------------------------

  it('handleOpenTemplateModal sets template and opens modal', () => {
    const { result } = renderHook(() => useWorkflowStudio());

    const tmpl = { id: 'blog', name: 'Blog Post' };
    act(() => result.current.handleOpenTemplateModal(tmpl));

    expect(result.current.selectedTemplate).toEqual(tmpl);
    expect(result.current.templateModalOpen).toBe(true);
  });

  it('handleCloseTemplateModal closes modal', () => {
    const { result } = renderHook(() => useWorkflowStudio());

    act(() => result.current.handleOpenTemplateModal({ id: 'x', name: 'X' }));
    act(() => result.current.handleCloseTemplateModal());

    expect(result.current.templateModalOpen).toBe(false);
  });

  // ---- Return shape -------------------------------------------------------

  it('returns expected properties', () => {
    const { result } = renderHook(() => useWorkflowStudio());

    const expected = [
      'studioTab',
      'availablePhases',
      'workflows',
      'templates',
      'loadingWorkflows',
      'selectedWorkflow',
      'selectedTemplate',
      'templateModalOpen',
      'loadWorkflowStudioData',
      'handleDeleteWorkflow',
      'handleExecuteWorkflow',
      'handleWorkflowSaved',
      'handleStudioTabChange',
      'handleSelectWorkflowForEdit',
      'handleOpenTemplateModal',
      'handleCloseTemplateModal',
    ];

    for (const key of expected) {
      expect(result.current).toHaveProperty(key);
    }
  });
});
