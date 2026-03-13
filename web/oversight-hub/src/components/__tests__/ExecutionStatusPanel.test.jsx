/**
 * ExecutionStatusPanel Component Tests
 *
 * Tests rendering of execution status, progress, per-phase results,
 * error states, final output, and execution history list.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ExecutionStatusPanel from '../ExecutionStatusPanel';

const DEFAULT_PROPS = {
  executionId: 'exec-123',
  executionStatus: 'running',
  executionProgress: 50,
  executionResults: {},
  executionFinalOutput: null,
  executionErrorMessage: '',
  executionPollingError: '',
  executionHistory: [],
  executionHistoryLoading: false,
  executionHistoryError: '',
  onSelectExecution: vi.fn(),
  onRefreshHistory: vi.fn(),
};

describe('ExecutionStatusPanel', () => {
  describe('null guard', () => {
    it('renders nothing when executionId is null', () => {
      const { container } = render(
        <ExecutionStatusPanel {...DEFAULT_PROPS} executionId={null} />
      );
      expect(container.firstChild).toBeNull();
    });

    it('renders nothing when executionId is undefined', () => {
      const { container } = render(
        <ExecutionStatusPanel {...DEFAULT_PROPS} executionId={undefined} />
      );
      expect(container.firstChild).toBeNull();
    });
  });

  describe('basic rendering', () => {
    it('renders execution ID', () => {
      render(<ExecutionStatusPanel {...DEFAULT_PROPS} />);
      expect(screen.getByText(/exec-123/)).toBeInTheDocument();
    });

    it('renders execution status chip', () => {
      render(<ExecutionStatusPanel {...DEFAULT_PROPS} />);
      expect(screen.getByText('running')).toBeInTheDocument();
    });

    it('renders progress percentage', () => {
      render(<ExecutionStatusPanel {...DEFAULT_PROPS} />);
      expect(screen.getByText('50%')).toBeInTheDocument();
    });

    it('renders "Execution Status" heading', () => {
      render(<ExecutionStatusPanel {...DEFAULT_PROPS} />);
      expect(screen.getByText('Execution Status')).toBeInTheDocument();
    });

    it('renders pending status chip when executionStatus is null', () => {
      render(
        <ExecutionStatusPanel {...DEFAULT_PROPS} executionStatus={null} />
      );
      expect(screen.getByText('pending')).toBeInTheDocument();
    });
  });

  describe('polling error', () => {
    it('shows polling error warning when executionPollingError is set', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionPollingError="Connection refused"
        />
      );
      expect(screen.getByText('Connection refused')).toBeInTheDocument();
    });

    it('does not show polling error when it is empty', () => {
      render(
        <ExecutionStatusPanel {...DEFAULT_PROPS} executionPollingError="" />
      );
      expect(screen.queryByRole('alert')).toBeNull();
    });
  });

  describe('error message', () => {
    it('shows execution error message alert', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionErrorMessage="Pipeline failed"
        />
      );
      expect(screen.getByText('Pipeline failed')).toBeInTheDocument();
    });
  });

  describe('phase results', () => {
    it('renders phase names from executionResults', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionResults={{
            research: { status: 'completed', output: 'Some research output' },
            draft: { status: 'running', output: null },
          }}
        />
      );
      expect(screen.getByText('research')).toBeInTheDocument();
      expect(screen.getByText('draft')).toBeInTheDocument();
    });

    it('renders phase status chip', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionResults={{
            research: { status: 'Completed', output: '' },
          }}
        />
      );
      // Status is normalised to lowercase
      expect(screen.getByText('completed')).toBeInTheDocument();
    });

    it('renders string output preview', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionResults={{
            research: { status: 'completed', output: 'Market analysis done.' },
          }}
        />
      );
      expect(screen.getByText('Market analysis done.')).toBeInTheDocument();
    });

    it('renders nested output.content preview', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionResults={{
            draft: {
              status: 'completed',
              output: { content: 'Blog draft text here.' },
            },
          }}
        />
      );
      expect(screen.getByText('Blog draft text here.')).toBeInTheDocument();
    });

    it('renders error as preview when output is absent', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionResults={{
            qa: { status: 'failed', output: null, error: 'LLM timeout' },
          }}
        />
      );
      expect(screen.getByText('LLM timeout')).toBeInTheDocument();
    });

    it('renders execution mode when present in metadata', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionResults={{
            research: {
              status: 'completed',
              output: {
                _phase_metadata: { execution_mode: 'llm-based' },
              },
            },
          }}
        />
      );
      expect(screen.getByText('Execution mode: llm-based')).toBeInTheDocument();
    });

    it('renders "pending" execution mode when metadata is absent', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionResults={{
            research: { status: 'running', output: null },
          }}
        />
      );
      expect(screen.getByText(/Execution mode: pending/)).toBeInTheDocument();
    });
  });

  describe('final output', () => {
    it('shows final output alert when executionFinalOutput is truthy', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionFinalOutput={{ content: 'Done' }}
        />
      );
      expect(screen.getByText(/Final output is available/)).toBeInTheDocument();
    });

    it('does not show final output alert when executionFinalOutput is null', () => {
      render(
        <ExecutionStatusPanel {...DEFAULT_PROPS} executionFinalOutput={null} />
      );
      expect(
        screen.queryByText(/Final output is available/)
      ).not.toBeInTheDocument();
    });
  });

  describe('execution history', () => {
    it('shows loading indicator while history is loading', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionHistoryLoading={true}
        />
      );
      expect(screen.getByText(/Loading execution history/)).toBeInTheDocument();
    });

    it('shows empty state when history is empty and not loading', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionHistory={[]}
          executionHistoryLoading={false}
        />
      );
      expect(screen.getByText(/No execution history yet/)).toBeInTheDocument();
    });

    it('shows history error when executionHistoryError is set', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionHistoryError="Failed to load"
        />
      );
      expect(screen.getByText('Failed to load')).toBeInTheDocument();
    });

    it('renders history items with their IDs', () => {
      const history = [
        { id: 'exec-001', execution_status: 'completed' },
        { id: 'exec-002', execution_status: 'failed' },
      ];
      render(
        <ExecutionStatusPanel {...DEFAULT_PROPS} executionHistory={history} />
      );
      expect(screen.getByText('exec-001')).toBeInTheDocument();
      expect(screen.getByText('exec-002')).toBeInTheDocument();
    });

    it('calls onSelectExecution when "View details" is clicked', () => {
      const onSelectExecution = vi.fn();
      const history = [{ id: 'exec-001', execution_status: 'completed' }];
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionHistory={history}
          onSelectExecution={onSelectExecution}
        />
      );
      fireEvent.click(screen.getByText('View details'));
      expect(onSelectExecution).toHaveBeenCalledWith('exec-001');
    });

    it('calls onRefreshHistory when Refresh is clicked', () => {
      const onRefreshHistory = vi.fn();
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          onRefreshHistory={onRefreshHistory}
        />
      );
      fireEvent.click(screen.getByText('Refresh'));
      expect(onRefreshHistory).toHaveBeenCalledTimes(1);
    });

    it('disables Refresh button while history is loading', () => {
      render(
        <ExecutionStatusPanel
          {...DEFAULT_PROPS}
          executionHistoryLoading={true}
        />
      );
      expect(screen.getByText('Refresh')).toBeDisabled();
    });
  });
});
