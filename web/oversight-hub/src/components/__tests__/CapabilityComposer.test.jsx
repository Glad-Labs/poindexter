import React from 'react';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react';
import CapabilityComposer from '../CapabilityComposer';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../services/capabilityTasksService', () => ({
  default: {
    listCapabilities: vi.fn(),
    createTask: vi.fn(),
    executeTask: vi.fn(),
  },
}));

vi.mock('../NaturalLanguageTaskComposer', () => ({
  default: () => <div data-testid="nl-task-composer" />,
}));

vi.mock('@/lib/logger', () => ({
  default: { debug: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));

import CapabilityTasksService from '../../services/capabilityTasksService';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const mockCapabilities = [
  {
    name: 'generate_blog_post',
    description: 'Generates a blog post',
    tags: ['content', 'writing'],
    cost_tier: 'cheap',
    input_schema: {
      parameters: [
        {
          name: 'topic',
          required: true,
          type: 'string',
          description: 'The topic',
        },
      ],
    },
    output_schema: { description: 'Returns blog post object' },
  },
  {
    name: 'summarize_text',
    description: 'Summarizes provided text',
    tags: ['nlp'],
    cost_tier: 'ultra_cheap',
    input_schema: { parameters: [] },
    output_schema: { description: 'Returns summary' },
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  CapabilityTasksService.listCapabilities.mockResolvedValue({
    capabilities: mockCapabilities,
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('CapabilityComposer — initial render', () => {
  it('shows loading spinner while fetching capabilities', () => {
    // Never resolves
    CapabilityTasksService.listCapabilities.mockReturnValue(
      new Promise(() => {})
    );
    render(<CapabilityComposer />);
    expect(
      document.querySelector('.MuiCircularProgress-root')
    ).toBeInTheDocument();
  });

  it('renders "Capability Composer" heading', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('Capability Composer')).toBeInTheDocument();
    });
  });

  it('renders "Manual Composition" and "Natural Language" tabs', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('Manual Composition')).toBeInTheDocument();
      expect(screen.getByText('Natural Language')).toBeInTheDocument();
    });
  });

  it('renders available capabilities after load', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
      expect(screen.getByText('summarize_text')).toBeInTheDocument();
    });
  });

  it('shows Available Capabilities count', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(
        screen.getByText('Available Capabilities (2)')
      ).toBeInTheDocument();
    });
  });

  it('shows error alert when listCapabilities rejects', async () => {
    CapabilityTasksService.listCapabilities.mockRejectedValue(
      new Error('API unavailable')
    );
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('API unavailable')).toBeInTheDocument();
    });
  });
});

describe('CapabilityComposer — tab navigation', () => {
  it('switches to Natural Language tab when clicked', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('Manual Composition')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Natural Language'));
    await waitFor(() => {
      expect(screen.getByTestId('nl-task-composer')).toBeInTheDocument();
    });
  });
});

describe('CapabilityComposer — capability card interaction', () => {
  it('opens capability details dialog when card is clicked', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });
    // Clicking the CapabilityCard opens a dialog
    fireEvent.click(screen.getAllByText('generate_blog_post')[0]);
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  it('shows "Add to Task" button in capability dialog', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });
    fireEvent.click(screen.getAllByText('generate_blog_post')[0]);
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Add to Task/i })
      ).toBeInTheDocument();
    });
  });

  it('adds a step when "Add to Task" is clicked', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });
    // Open dialog
    fireEvent.click(screen.getAllByText('generate_blog_post')[0]);
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Add to Task/i })
      ).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Add to Task/i }));
    // After adding, the step should appear in the Steps table
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post_output')).toBeInTheDocument();
    });
  });

  it('closes capability dialog when Close is clicked', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });
    fireEvent.click(screen.getAllByText('generate_blog_post')[0]);
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});

describe('CapabilityComposer — validation', () => {
  it('Save Task button is disabled when task name is empty', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });

    // Add a step so the Save Task button appears
    fireEvent.click(screen.getAllByText('generate_blog_post')[0]);
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Add to Task/i })
      ).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Add to Task/i }));

    await waitFor(() => {
      expect(screen.getByText('generate_blog_post_output')).toBeInTheDocument();
    });

    // Save Task button is disabled when taskName is empty (disabled={!taskName.trim()})
    const saveBtn = screen.getByText('Save Task').closest('button');
    expect(saveBtn).toBeDisabled();
  });

  it('Save Task button is not rendered with 0 steps', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });

    // No steps added — "Click on a capability" placeholder message should show
    expect(
      screen.getByText('Click on a capability to add it to your task')
    ).toBeInTheDocument();

    // Save Task button is not rendered when steps.length === 0
    expect(screen.queryByText('Save Task')).not.toBeInTheDocument();
  });

  it('shows error when Execute is clicked with steps but no task name', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post')).toBeInTheDocument();
    });

    // Add step
    fireEvent.click(screen.getAllByText('generate_blog_post')[0]);
    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /Add to Task/i }));
    });
    await waitFor(() => {
      expect(screen.getByText('generate_blog_post_output')).toBeInTheDocument();
    });

    // Execute button — disabled when !taskName.trim() — so it's disabled too
    const executeBtn = screen.getByText('Execute').closest('button');
    expect(executeBtn).toBeDisabled();
  });
});

// Helper: add a capability step, fill task name, then click Save Task
async function addStepAndFillName(container, capName, taskName) {
  await waitFor(() => {
    expect(screen.getByText(capName)).toBeInTheDocument();
  });
  // Open details dialog and add
  fireEvent.click(screen.getAllByText(capName)[0]);
  await waitFor(() => {
    expect(
      screen.getByRole('button', { name: /Add to Task/i })
    ).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole('button', { name: /Add to Task/i }));
  await waitFor(() => {
    // Step row in table
    expect(screen.getByText(`${capName}_output`)).toBeInTheDocument();
  });
  // Fill task name — MUI TextField renders <input>; Task Name is the first text input
  const inputs = container.querySelectorAll(
    'input[type="text"], input:not([type])'
  );
  // First non-hidden input is the Task Name field
  const taskNameInput = Array.from(inputs).find(
    (inp) => !inp.type || inp.type === 'text'
  );
  fireEvent.change(taskNameInput, { target: { value: taskName } });
}

describe('CapabilityComposer — task creation', () => {
  it('calls createTask with correct arguments', async () => {
    CapabilityTasksService.createTask.mockResolvedValue({ id: 'task-123' });
    const { container } = render(<CapabilityComposer />);

    await addStepAndFillName(
      container,
      'generate_blog_post',
      'Blog Creation Task'
    );

    await act(async () => {
      fireEvent.click(screen.getByText('Save Task'));
    });

    await waitFor(() => {
      expect(CapabilityTasksService.createTask).toHaveBeenCalledWith(
        'Blog Creation Task',
        expect.any(String),
        expect.any(Array),
        expect.any(Array)
      );
    });
  });

  it('shows success alert after successful task creation', async () => {
    CapabilityTasksService.createTask.mockResolvedValue({ id: 'task-999' });
    const { container } = render(<CapabilityComposer />);

    await addStepAndFillName(container, 'generate_blog_post', 'My Task');

    await act(async () => {
      fireEvent.click(screen.getByText('Save Task'));
    });

    await waitFor(() => {
      expect(
        screen.getByText(/Task "My Task" created successfully/)
      ).toBeInTheDocument();
    });
  });

  it('shows error alert when createTask rejects', async () => {
    CapabilityTasksService.createTask.mockRejectedValue(
      new Error('Failed to create task')
    );
    const { container } = render(<CapabilityComposer />);

    await addStepAndFillName(container, 'generate_blog_post', 'My Task');

    await act(async () => {
      fireEvent.click(screen.getByText('Save Task'));
    });

    await waitFor(() => {
      expect(screen.getByText('Failed to create task')).toBeInTheDocument();
    });
  });
});

describe('CapabilityComposer — tag management', () => {
  it('adds a tag when tag input is filled and Add Tag is clicked', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('Capability Composer')).toBeInTheDocument();
    });

    const tagInput = screen.getByPlaceholderText('Add tag...');
    // Use a unique tag that won't conflict with capability chip labels
    fireEvent.change(tagInput, { target: { value: 'my-unique-tag' } });
    const addBtn = screen.getByText('Add');
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText('my-unique-tag')).toBeInTheDocument();
    });
  });

  it('does not add duplicate tags', async () => {
    render(<CapabilityComposer />);
    await waitFor(() => {
      expect(screen.getByText('Capability Composer')).toBeInTheDocument();
    });

    const tagInput = screen.getByPlaceholderText('Add tag...');
    const getAddBtn = () => screen.getByText('Add');

    fireEvent.change(tagInput, { target: { value: 'my-tag' } });
    fireEvent.click(getAddBtn());

    // Tag should be visible
    await waitFor(() => {
      expect(screen.getByText('my-tag')).toBeInTheDocument();
    });

    // Try to add same tag again
    fireEvent.change(tagInput, { target: { value: 'my-tag' } });
    fireEvent.click(getAddBtn());

    // Still only one chip for this tag
    const chips = screen.getAllByText('my-tag');
    expect(chips.length).toBe(1);
  });
});
