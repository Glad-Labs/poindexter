/**
 * useApprovalQueue
 *
 * Manages fetching, pagination, filtering, sorting, WebSocket subscriptions,
 * and individual approve/reject API calls for the Approval Queue.
 *
 * Extracted from ApprovalQueue.jsx (#311).
 */
import { useState, useEffect, useCallback } from 'react';
import logger from '@/lib/logger';
import { getApiUrl } from '../config/apiConfig';

const ITEMS_PER_PAGE = 10;

const getApiBaseUrl = () => getApiUrl();

/**
 * @param {object} params
 * @param {Function} [params.onSuccess] - called with a success message string
 * @param {Function} [params.onError]   - called with an error message string
 */
const useApprovalQueue = ({ onSuccess, onError } = {}) => {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState('created_at');
  const [sortOrder] = useState('desc');
  const [taskTypeFilter, setTaskTypeFilter] = useState('');
  const [processingTaskId, setProcessingTaskId] = useState(null);

  // Dialog state for single approve
  const [approveDialogOpen, setApproveDialogOpen] = useState(false);
  const [approveFeedback, setApproveFeedback] = useState('');

  // Dialog state for single reject
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('Content quality');
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [allowRevisions, setAllowRevisions] = useState(true);

  // Preview dialog
  const [previewOpen, setPreviewOpen] = useState(false);
  const [selectedTask, setSelectedTask] = useState(null);

  // ---- Fetch ----------------------------------------------------------------

  const fetchPendingTasks = useCallback(async () => {
    setLoading(true);
    if (onError) onError(null);

    try {
      const offset = (page - 1) * ITEMS_PER_PAGE;
      const params = new URLSearchParams({
        limit: ITEMS_PER_PAGE,
        offset,
        sort_by: sortBy,
        sort_order: sortOrder,
      });
      if (taskTypeFilter) {
        params.append('task_type', taskTypeFilter);
      }

      const response = await fetch(
        `${getApiBaseUrl()}/api/tasks/pending-approval?${params}`,
        {
          method: 'GET',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        }
      );

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Unauthorized - please log in');
        }
        throw new Error(
          `Failed to fetch pending approvals: ${response.statusText}`
        );
      }

      const data = await response.json();
      setTasks(data.tasks || []);
    } catch (err) {
      logger.error('[useApprovalQueue] Failed to fetch:', err);
      if (onError) onError(err.message);
      setTasks([]);
    } finally {
      setLoading(false);
    }
  }, [page, sortBy, sortOrder, taskTypeFilter, onError]);

  useEffect(() => {
    fetchPendingTasks();
  }, [fetchPendingTasks]);

  // ---- WebSocket real-time updates ------------------------------------------

  useEffect(() => {
    const wsConnections = new Map();

    const subscribeToUpdates = () => {
      const apiBaseUrl = getApiBaseUrl();
      const wsProtocol = apiBaseUrl.startsWith('https') ? 'wss' : 'ws';
      const wsHost = apiBaseUrl.replace(/^https?:\/\//, '');

      tasks.forEach((task) => {
        if (!wsConnections.has(task.task_id)) {
          try {
            const ws = new WebSocket(
              `${wsProtocol}://${wsHost}/api/ws/approval/${task.task_id}`
            );

            ws.onopen = () => {
              if (process.env.NODE_ENV === 'development') {
                logger.log(
                  `[useApprovalQueue] WebSocket connected for task: ${task.task_id}`
                );
              }
            };

            ws.onmessage = (event) => {
              try {
                const message = JSON.parse(event.data);
                if (message.type === 'approval_status') {
                  setTasks((prevTasks) =>
                    prevTasks.map((t) => {
                      if (t.task_id === message.task_id) {
                        return {
                          ...t,
                          status: message.status,
                          approval_feedback: message.feedback,
                          approval_timestamp: message.timestamp,
                        };
                      }
                      return t;
                    })
                  );
                  if (onSuccess) {
                    onSuccess(
                      `Task ${message.status}: ${message.task_id.substring(0, 8)}`
                    );
                  }
                }
              } catch (err) {
                if (process.env.NODE_ENV === 'development') {
                  logger.error(
                    '[useApprovalQueue] Failed to parse WebSocket message:',
                    err
                  );
                }
              }
            };

            ws.onerror = (err) => {
              if (process.env.NODE_ENV === 'development') {
                logger.error(
                  `[useApprovalQueue] WebSocket error for task ${task.task_id}:`,
                  err
                );
              }
            };

            ws.onclose = () => {
              if (process.env.NODE_ENV === 'development') {
                logger.log(
                  `[useApprovalQueue] WebSocket disconnected for task: ${task.task_id}`
                );
              }
              wsConnections.delete(task.task_id);
            };

            wsConnections.set(task.task_id, ws);
          } catch (err) {
            if (process.env.NODE_ENV === 'development') {
              logger.error(
                `[useApprovalQueue] Failed to connect WebSocket for ${task.task_id}:`,
                err
              );
            }
          }
        }
      });
    };

    if (tasks.length > 0) {
      subscribeToUpdates();
    }

    return () => {
      wsConnections.forEach((ws) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.close();
        }
      });
      wsConnections.clear();
    };
  }, [tasks, onSuccess]);

  // ---- Single approve -------------------------------------------------------

  const handleApproveClick = useCallback((task) => {
    setSelectedTask(task);
    setApproveFeedback('');
    setApproveDialogOpen(true);
  }, []);

  const handleApprovalSubmit = useCallback(async () => {
    if (!selectedTask) return;
    setProcessingTaskId(selectedTask.task_id);
    if (onError) onError(null);

    try {
      const response = await fetch(
        `${getApiBaseUrl()}/api/tasks/${selectedTask.task_id}/approve`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            approved: true,
            auto_publish: true,
            human_feedback: approveFeedback || undefined,
          }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail?.message || 'Failed to approve task');
      }

      await response.json();
      if (onSuccess) {
        onSuccess(`Task approved and published: ${selectedTask.task_name}`);
      }
      setApproveDialogOpen(false);

      setTimeout(() => {
        fetchPendingTasks();
        if (onSuccess) onSuccess(null);
      }, 1500);
    } catch (err) {
      if (onError) onError(err.message);
    } finally {
      setProcessingTaskId(null);
    }
  }, [selectedTask, approveFeedback, fetchPendingTasks, onSuccess, onError]);

  // ---- Single reject --------------------------------------------------------

  const handleRejectClick = useCallback((task) => {
    setSelectedTask(task);
    setRejectReason('Content quality');
    setRejectFeedback('');
    setAllowRevisions(true);
    setRejectDialogOpen(true);
  }, []);

  const handleRejectionSubmit = useCallback(async () => {
    if (!selectedTask || !rejectFeedback) {
      if (onError) onError('Please provide feedback');
      return;
    }
    setProcessingTaskId(selectedTask.task_id);
    if (onError) onError(null);

    try {
      const response = await fetch(
        `${getApiBaseUrl()}/api/tasks/${selectedTask.task_id}/reject`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            reason: rejectReason,
            feedback: rejectFeedback,
            allow_revisions: allowRevisions,
          }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail?.message || 'Failed to reject task');
      }

      await response.json();
      if (onSuccess) onSuccess(`Task rejected: ${selectedTask.task_name}`);
      setRejectDialogOpen(false);

      setTimeout(() => {
        fetchPendingTasks();
        if (onSuccess) onSuccess(null);
      }, 1500);
    } catch (err) {
      if (onError) onError(err.message);
    } finally {
      setProcessingTaskId(null);
    }
  }, [
    selectedTask,
    rejectReason,
    rejectFeedback,
    allowRevisions,
    fetchPendingTasks,
    onSuccess,
    onError,
  ]);

  // ---- Preview dialog -------------------------------------------------------

  const handlePreviewOpen = useCallback((task) => {
    setSelectedTask(task);
    setPreviewOpen(true);
  }, []);

  const handlePreviewClose = useCallback(() => {
    setPreviewOpen(false);
    setSelectedTask(null);
  }, []);

  // ---- Pagination / filter / sort controls ----------------------------------

  const handlePageChange = useCallback((_event, newPage) => {
    setPage(newPage);
  }, []);

  const handleSortChange = useCallback((e) => {
    setSortBy(e.target.value);
    setPage(1);
  }, []);

  const handleTaskTypeFilterChange = useCallback((event) => {
    const value = event.target.value;
    setTaskTypeFilter(value === 'all' ? '' : value);
    setPage(1);
  }, []);

  return {
    tasks,
    setTasks,
    loading,
    page,
    ITEMS_PER_PAGE,
    sortBy,
    taskTypeFilter,
    processingTaskId,

    // Single approve
    approveDialogOpen,
    setApproveDialogOpen,
    approveFeedback,
    setApproveFeedback,
    handleApproveClick,
    handleApprovalSubmit,

    // Single reject
    rejectDialogOpen,
    setRejectDialogOpen,
    rejectReason,
    setRejectReason,
    rejectFeedback,
    setRejectFeedback,
    allowRevisions,
    setAllowRevisions,
    handleRejectClick,
    handleRejectionSubmit,

    // Preview
    previewOpen,
    selectedTask,
    handlePreviewOpen,
    handlePreviewClose,

    // Controls
    fetchPendingTasks,
    handlePageChange,
    handleSortChange,
    handleTaskTypeFilterChange,
  };
};

export default useApprovalQueue;
