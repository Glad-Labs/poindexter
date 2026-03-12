/**
 * Component Tests for NaturalLanguageTaskComposer
 *
 * Tests cover:
 * 1. Compact and full rendering modes
 * 2. Input validation (empty request)
 * 3. Task composition flow (composeTaskFromNaturalLanguage)
 * 4. Auto-execute flow (composeAndExecuteTask)
 * 5. Execute composed task
 * 6. Error handling
 * 7. "Compose Another" reset behavior
 * 8. Callback props (onTaskComposed, onTaskExecuted)
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import NaturalLanguageTaskComposer from '../NaturalLanguageTaskComposer';

// Mock NL composer service
vi.mock('../../services/naturalLanguageComposerService', () => ({
  composeTaskFromNaturalLanguage: vi.fn(),
  composeAndExecuteTask: vi.fn(),
}));

// Mock capability tasks service
vi.mock('../../services/capabilityTasksService', () => ({
  default: {
    executeTask: vi.fn(),
  },
}));

import {
  composeTaskFromNaturalLanguage,
  composeAndExecuteTask,
} from '../../services/naturalLanguageComposerService';
import CapabilityTasksService from '../../services/capabilityTasksService';

const mockTaskDef = {
  id: 'task-abc-123',
  name: 'Write a blog post about AI',
  category: 'content',
};

const mockCompositionSuccess = {
  success: true,
  task_definition: mockTaskDef,
  explanation: 'This task will generate a blog post on AI trends.',
};

const mockExecutionResult = {
  execution_id: 'exec-xyz-789',
  status: 'running',
};

// ============================================================================
// COMPACT VIEW RENDERING
// ============================================================================

describe('NaturalLanguageTaskComposer — Compact View', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders compact card with subtitle', () => {
    render(<NaturalLanguageTaskComposer compact />);

    expect(
      screen.getByText(/compose task from natural language/i)
    ).toBeInTheDocument();
  });

  it('renders Suggest Task and Compose & Execute buttons', () => {
    render(<NaturalLanguageTaskComposer compact />);

    expect(
      screen.getByRole('button', { name: /suggest task/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /compose & execute/i })
    ).toBeInTheDocument();
  });

  it('disables buttons when request is empty', () => {
    render(<NaturalLanguageTaskComposer compact />);

    expect(
      screen.getByRole('button', { name: /suggest task/i })
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: /compose & execute/i })
    ).toBeDisabled();
  });

  it('enables buttons after typing a request', async () => {
    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a post about AI');

    expect(
      screen.getByRole('button', { name: /suggest task/i })
    ).not.toBeDisabled();
    expect(
      screen.getByRole('button', { name: /compose & execute/i })
    ).not.toBeDisabled();
  });
});

// ============================================================================
// FULL VIEW RENDERING
// ============================================================================

describe('NaturalLanguageTaskComposer — Full View', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders full view card header when compact is false', () => {
    render(<NaturalLanguageTaskComposer />);

    expect(
      screen.getByText('Natural Language Task Composer')
    ).toBeInTheDocument();
  });

  it('renders Review Suggested Task button in full view', () => {
    render(<NaturalLanguageTaskComposer />);

    expect(
      screen.getByRole('button', { name: /review suggested task/i })
    ).toBeInTheDocument();
  });

  it('renders Compose & Auto-Execute button in full view', () => {
    render(<NaturalLanguageTaskComposer />);

    expect(
      screen.getByRole('button', { name: /compose & auto-execute/i })
    ).toBeInTheDocument();
  });
});

// ============================================================================
// COMPOSITION FLOW
// ============================================================================

describe('NaturalLanguageTaskComposer — Composition Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls composeTaskFromNaturalLanguage with the entered request', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');

    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      expect(composeTaskFromNaturalLanguage).toHaveBeenCalledWith(
        'Write a blog post about AI',
        expect.objectContaining({ autoExecute: false, saveTask: true })
      );
    });
  });

  it('shows composed task name after successful composition', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      expect(
        screen.getByText('Write a blog post about AI')
      ).toBeInTheDocument();
    });

    expect(
      screen.getByText(/this task will generate a blog post/i)
    ).toBeInTheDocument();
  });

  it('calls onTaskComposed callback on successful composition', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);
    const onTaskComposed = vi.fn();

    render(
      <NaturalLanguageTaskComposer compact onTaskComposed={onTaskComposed} />
    );

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      expect(onTaskComposed).toHaveBeenCalledWith(mockTaskDef);
    });
  });

  it('shows Execute Now button after successful composition', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /execute now/i })
      ).toBeInTheDocument();
    });
  });
});

// ============================================================================
// AUTO-EXECUTE FLOW
// ============================================================================

describe('NaturalLanguageTaskComposer — Auto-Execute Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls composeAndExecuteTask when Compose & Execute is clicked', async () => {
    composeAndExecuteTask.mockResolvedValue({
      success: true,
      execution_id: 'exec-xyz-789',
      task_definition: mockTaskDef,
      explanation: 'Auto-executed task',
    });

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /compose & execute/i }));

    await waitFor(() => {
      expect(composeAndExecuteTask).toHaveBeenCalledWith(
        'Write a blog post about AI',
        expect.objectContaining({ saveTask: true })
      );
    });
  });

  it('calls onTaskExecuted callback after auto-execute', async () => {
    const autoExecuteResult = {
      success: true,
      execution_id: 'exec-xyz-789',
      task_definition: mockTaskDef,
    };
    composeAndExecuteTask.mockResolvedValue(autoExecuteResult);
    const onTaskExecuted = vi.fn();

    render(
      <NaturalLanguageTaskComposer compact onTaskExecuted={onTaskExecuted} />
    );

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /compose & execute/i }));

    await waitFor(() => {
      expect(onTaskExecuted).toHaveBeenCalledWith(autoExecuteResult);
    });
  });
});

// ============================================================================
// EXECUTE COMPOSED TASK
// ============================================================================

describe('NaturalLanguageTaskComposer — Execute Composed Task', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls CapabilityTasksService.executeTask when Execute Now is clicked', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);
    CapabilityTasksService.executeTask.mockResolvedValue(mockExecutionResult);

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /execute now/i })
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /execute now/i }));

    await waitFor(() => {
      expect(CapabilityTasksService.executeTask).toHaveBeenCalledWith(
        'task-abc-123'
      );
    });
  });

  it('shows success alert with execution_id after execution', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);
    CapabilityTasksService.executeTask.mockResolvedValue(mockExecutionResult);

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      screen.getByRole('button', { name: /execute now/i });
    });

    fireEvent.click(screen.getByRole('button', { name: /execute now/i }));

    await waitFor(() => {
      expect(screen.getByText(/exec-xyz-789/)).toBeInTheDocument();
    });
  });
});

// ============================================================================
// ERROR HANDLING
// ============================================================================

describe('NaturalLanguageTaskComposer — Error Handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows error alert when composition fails', async () => {
    composeTaskFromNaturalLanguage.mockRejectedValue(
      new Error('LLM service unavailable')
    );

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      expect(screen.getByText(/llm service unavailable/i)).toBeInTheDocument();
    });
  });

  it('shows error when composition returns success: false', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue({
      success: false,
      error: 'Could not parse intent from request',
    });

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'do something vague');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/could not parse intent from request/i)
      ).toBeInTheDocument();
    });
  });

  it('shows error when execution fails', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);
    CapabilityTasksService.executeTask.mockRejectedValue(
      new Error('Execution timeout')
    );

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      screen.getByRole('button', { name: /execute now/i });
    });

    fireEvent.click(screen.getByRole('button', { name: /execute now/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/execution failed.*execution timeout/i)
      ).toBeInTheDocument();
    });
  });
});

// ============================================================================
// RESET BEHAVIOR
// ============================================================================

describe('NaturalLanguageTaskComposer — Reset Behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('resets form when Compose Another is clicked', async () => {
    composeTaskFromNaturalLanguage.mockResolvedValue(mockCompositionSuccess);

    render(<NaturalLanguageTaskComposer compact />);

    const textarea = screen.getByPlaceholderText(
      /describe what you want to accomplish/i
    );
    await userEvent.type(textarea, 'Write a blog post about AI');
    fireEvent.click(screen.getByRole('button', { name: /suggest task/i }));

    await waitFor(() => {
      screen.getByRole('button', { name: /compose another/i });
    });

    fireEvent.click(screen.getByRole('button', { name: /compose another/i }));

    // Should be back to the input form
    expect(
      screen.getByPlaceholderText(/describe what you want to accomplish/i)
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /suggest task/i })
    ).toBeInTheDocument();
  });
});
