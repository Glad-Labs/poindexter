import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import OrchestratorMessageCard from '../OrchestratorMessageCard';

describe('OrchestratorMessageCard Component', () => {
  it('renders the header label', () => {
    render(
      <OrchestratorMessageCard headerIcon="✨" headerLabel="Command Ready">
        <span>Content</span>
      </OrchestratorMessageCard>
    );
    expect(screen.getByText('Command Ready')).toBeInTheDocument();
  });

  it('renders the header icon', () => {
    render(
      <OrchestratorMessageCard headerIcon="🚀" headerLabel="Test">
        <span>Content</span>
      </OrchestratorMessageCard>
    );
    expect(screen.getByText('🚀')).toBeInTheDocument();
  });

  it('renders children in the card content area', () => {
    render(
      <OrchestratorMessageCard headerLabel="Test">
        <span>Child content here</span>
      </OrchestratorMessageCard>
    );
    expect(screen.getByText('Child content here')).toBeInTheDocument();
  });

  it('renders metadata items as label: value pairs', () => {
    const metadata = [
      { label: 'Type', value: 'generate' },
      { label: 'Model', value: 'GPT-4' },
    ];
    render(
      <OrchestratorMessageCard headerLabel="Test" metadata={metadata}>
        <span>Content</span>
      </OrchestratorMessageCard>
    );
    expect(screen.getByText('Type:')).toBeInTheDocument();
    expect(screen.getByText('generate')).toBeInTheDocument();
    expect(screen.getByText('Model:')).toBeInTheDocument();
    expect(screen.getByText('GPT-4')).toBeInTheDocument();
  });

  it('does not render metadata section when no metadata is provided', () => {
    render(
      <OrchestratorMessageCard headerLabel="Test" metadata={[]}>
        <span>Content</span>
      </OrchestratorMessageCard>
    );
    // No label/value spans should be present
    expect(screen.queryByText(/:/)).not.toBeInTheDocument();
  });

  describe('Expand/collapse functionality', () => {
    it('renders expand button when expandedContent is provided', () => {
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          expandedContent={<span>Expanded Content</span>}
        >
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      // The expand icon button should be present
      expect(document.querySelector('.MuiIconButton-root')).toBeInTheDocument();
    });

    it('does not render expand button when no expandedContent', () => {
      render(
        <OrchestratorMessageCard headerLabel="Test">
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      expect(document.querySelector('.MuiIconButton-root')).not.toBeInTheDocument();
    });

    it('expanded content is hidden by default', () => {
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          expandedContent={<span>Hidden Details</span>}
        >
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      expect(screen.queryByText('Hidden Details')).not.toBeInTheDocument();
    });

    it('shows expanded content when expandedDefaultOpen is true', () => {
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          expandedContent={<span>Visible Details</span>}
          expandedDefaultOpen={true}
        >
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      expect(screen.getByText('Visible Details')).toBeInTheDocument();
    });

    it('shows expanded content after clicking expand button', () => {
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          expandedContent={<span>Toggle Content</span>}
        >
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      const expandButton = document.querySelector('.MuiIconButton-root');
      // Initially collapsed
      expect(screen.queryByText('Toggle Content')).not.toBeInTheDocument();

      // After clicking, content becomes visible
      fireEvent.click(expandButton);
      expect(screen.getByText('Toggle Content')).toBeInTheDocument();
    });

    it('calls onExpand callback when expanding', () => {
      const onExpand = vi.fn();
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          expandedContent={<span>Details</span>}
          onExpand={onExpand}
        >
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      const expandButton = document.querySelector('.MuiIconButton-root');
      fireEvent.click(expandButton);
      expect(onExpand).toHaveBeenCalledTimes(1);
    });

    it('calls onCollapse callback when collapsing', () => {
      const onCollapse = vi.fn();
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          expandedContent={<span>Details</span>}
          expandedDefaultOpen={true}
          onCollapse={onCollapse}
        >
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      const expandButton = document.querySelector('.MuiIconButton-root');
      fireEvent.click(expandButton);
      expect(onCollapse).toHaveBeenCalledTimes(1);
    });
  });

  describe('Footer actions', () => {
    it('renders footer action buttons', () => {
      const footerActions = [
        { label: 'Execute', onClick: vi.fn(), variant: 'contained' },
        { label: 'Cancel', onClick: vi.fn(), variant: 'outlined' },
      ];
      render(
        <OrchestratorMessageCard headerLabel="Test" footerActions={footerActions}>
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      expect(screen.getByText('Execute')).toBeInTheDocument();
      expect(screen.getByText('Cancel')).toBeInTheDocument();
    });

    it('calls onClick when footer action button is clicked', () => {
      const handleExecute = vi.fn();
      const footerActions = [
        { label: 'Execute', onClick: handleExecute, variant: 'contained' },
      ];
      render(
        <OrchestratorMessageCard headerLabel="Test" footerActions={footerActions}>
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      fireEvent.click(screen.getByText('Execute'));
      expect(handleExecute).toHaveBeenCalledTimes(1);
    });

    it('disables footer action button when disabled is true', () => {
      const footerActions = [
        { label: 'Execute', onClick: vi.fn(), disabled: true },
      ];
      render(
        <OrchestratorMessageCard headerLabel="Test" footerActions={footerActions}>
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      expect(screen.getByText('Execute').closest('button')).toBeDisabled();
    });
  });

  describe('Header actions', () => {
    it('renders header action components', () => {
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          headerActions={[<span key="badge">HIGH</span>]}
        >
          <span>Main</span>
        </OrchestratorMessageCard>
      );
      expect(screen.getByText('HIGH')).toBeInTheDocument();
    });
  });

  describe('Custom content', () => {
    it('renders customContent instead of children when provided', () => {
      render(
        <OrchestratorMessageCard
          headerLabel="Test"
          customContent={<div>Custom Content Area</div>}
        >
          <span>Should not render</span>
        </OrchestratorMessageCard>
      );
      expect(screen.getByText('Custom Content Area')).toBeInTheDocument();
      expect(screen.queryByText('Should not render')).not.toBeInTheDocument();
    });
  });
});
