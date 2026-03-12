/**
 * useWorkflowStudio
 *
 * Owns all state and data-fetching for the Workflow Studio accordion section
 * in UnifiedServicesPanel. Extracted from UnifiedServicesPanel.jsx (#304).
 *
 * State managed:
 *   studioTab, availablePhases, workflows, templates,
 *   loadingWorkflows, selectedWorkflow, selectedTemplate, templateModalOpen
 *
 * Exposes loadWorkflowStudioData() so the parent can trigger a fetch when the
 * accordion expands.
 */
import { useState, useCallback } from 'react';
import logger from '@/lib/logger';
import * as workflowBuilderService from '../services/workflowBuilderService';
import * as workflowManagementService from '../services/workflowManagementService';

const DEFAULT_TEMPLATES = [
  {
    id: 'blog_post',
    name: 'Blog Post',
    description:
      'Full blog post generation with research, drafting, assessment, refinement',
    phase_count: 7,
    is_template: true,
  },
  {
    id: 'social_media',
    name: 'Social Media',
    description: 'Quick social media content generation',
    phase_count: 5,
    is_template: true,
  },
  {
    id: 'email',
    name: 'Email',
    description: 'Email content generation with assessment',
    phase_count: 4,
    is_template: true,
  },
];

/**
 * @param {object} params
 * @param {Function} params.onError - called with an error message string when
 *   a data-fetch fails; lets the parent surface the error in a shared Alert.
 */
const useWorkflowStudio = ({ onError } = {}) => {
  const [studioTab, setStudioTab] = useState(0);
  const [availablePhases, setAvailablePhases] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loadingWorkflows, setLoadingWorkflows] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [templateModalOpen, setTemplateModalOpen] = useState(false);

  const loadWorkflowStudioData = useCallback(async () => {
    setLoadingWorkflows(true);
    try {
      logger.log('[useWorkflowStudio] Loading workflow studio data...');

      const phasesRes = await workflowBuilderService.getAvailablePhases();
      setAvailablePhases(phasesRes.phases || []);

      const workflowsRes = await workflowBuilderService.listWorkflows({
        limit: 100,
      });
      setWorkflows(workflowsRes.workflows || []);

      setTemplates(DEFAULT_TEMPLATES);
    } catch (err) {
      const errorMsg =
        err?.message || String(err) || 'Unknown error loading workflow data';
      logger.error('[useWorkflowStudio] Error loading workflow data:', err);
      if (onError) {
        onError(`Workflow Error: ${errorMsg}`);
      }
    } finally {
      setLoadingWorkflows(false);
    }
  }, [onError]);

  const handleDeleteWorkflow = useCallback(
    async (workflowId) => {
      if (!window.confirm('Are you sure you want to delete this workflow?')) {
        return;
      }
      try {
        await workflowBuilderService.deleteWorkflow(workflowId);
        setWorkflows((w) => w.filter((wf) => wf.id !== workflowId));
      } catch (err) {
        if (onError) {
          onError(err.message);
        }
      }
    },
    [onError]
  );

  const handleExecuteWorkflow = useCallback(
    async (workflowId) => {
      try {
        await workflowManagementService.executeWorkflow(workflowId);
        logger.log(
          '[useWorkflowStudio] Workflow execution started:',
          workflowId
        );
      } catch (err) {
        logger.error('[useWorkflowStudio] Failed to execute workflow:', err);
        if (onError) {
          onError(err.message);
        }
      }
    },
    [onError]
  );

  const handleWorkflowSaved = useCallback((newWorkflow) => {
    setWorkflows((w) => [...w, newWorkflow]);
    setStudioTab(1); // Switch to My Workflows tab
  }, []);

  const handleStudioTabChange = useCallback((_event, newValue) => {
    setStudioTab(newValue);
  }, []);

  const handleSelectWorkflowForEdit = useCallback((workflow) => {
    setSelectedWorkflow(workflow);
    setStudioTab(0);
  }, []);

  const handleOpenTemplateModal = useCallback((template) => {
    setSelectedTemplate(template);
    setTemplateModalOpen(true);
  }, []);

  const handleCloseTemplateModal = useCallback(() => {
    setTemplateModalOpen(false);
  }, []);

  return {
    studioTab,
    availablePhases,
    workflows,
    templates,
    loadingWorkflows,
    selectedWorkflow,
    selectedTemplate,
    templateModalOpen,
    loadWorkflowStudioData,
    handleDeleteWorkflow,
    handleExecuteWorkflow,
    handleWorkflowSaved,
    handleStudioTabChange,
    handleSelectWorkflowForEdit,
    handleOpenTemplateModal,
    handleCloseTemplateModal,
  };
};

export default useWorkflowStudio;
