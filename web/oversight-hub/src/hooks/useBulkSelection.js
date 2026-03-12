/**
 * useBulkSelection
 *
 * Generic checkbox-selection hook for a list of items.
 * Also owns the bulk approve and bulk reject API calls for ApprovalQueue.
 *
 * Extracted from ApprovalQueue.jsx (#311).
 *
 * @param {object} params
 * @param {Array}    params.items      - the current page of items (used for select-all)
 * @param {string}   params.itemIdKey  - key on each item that gives its unique ID (e.g. "task_id")
 * @param {Function} [params.onSuccess] - called with a success message string
 * @param {Function} [params.onError]   - called with an error message string
 * @param {Function} [params.onRefresh] - called after a bulk operation completes to re-fetch
 */
import { useState, useCallback } from 'react';
import logger from '@/lib/logger';
import { getApiUrl } from '../config/apiConfig';

const getApiBaseUrl = () => getApiUrl();

const useBulkSelection = ({
  items = [],
  itemIdKey = 'id',
  onSuccess,
  onError,
  onRefresh,
} = {}) => {
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkApproveDialogOpen, setBulkApproveDialogOpen] = useState(false);
  const [bulkRejectDialogOpen, setBulkRejectDialogOpen] = useState(false);
  const [bulkRejectReason, setBulkRejectReason] = useState('Content quality');
  const [bulkRejectFeedback, setBulkRejectFeedback] = useState('');
  const [bulkAllowRevisions, setBulkAllowRevisions] = useState(true);
  const [bulkApproveFeedback, setBulkApproveFeedback] = useState('');
  const [bulkOperationLoading, setBulkOperationLoading] = useState(false);

  // ---- Selection helpers ----------------------------------------------------

  const handleToggleSelect = useCallback((id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const handleSelectAll = useCallback(() => {
    setSelectedIds(new Set(items.map((item) => item[itemIdKey])));
  }, [items, itemIdKey]);

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  // ---- Bulk approve ---------------------------------------------------------

  const handleBulkApproveClick = useCallback(() => {
    if (selectedIds.size === 0) {
      if (onError) onError('Please select at least one task');
      return;
    }
    setBulkApproveDialogOpen(true);
  }, [selectedIds.size, onError]);

  const handleBulkApproveSubmit = useCallback(async () => {
    setBulkOperationLoading(true);
    if (onError) onError(null);

    try {
      const response = await fetch(
        `${getApiBaseUrl()}/api/tasks/bulk-approve`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task_ids: Array.from(selectedIds),
            feedback: bulkApproveFeedback || '',
            reviewer_notes: bulkApproveFeedback || '',
          }),
        }
      );

      if (!response.ok) {
        throw new Error(`Failed to approve tasks: ${response.statusText}`);
      }

      const result = await response.json();
      if (onSuccess) {
        onSuccess(
          `Bulk approval completed: ${result.succeeded_count} approved, ${result.failed_count} failed`
        );
      }
      setBulkApproveDialogOpen(false);
      setSelectedIds(new Set());
      setBulkApproveFeedback('');

      setTimeout(() => {
        if (onRefresh) onRefresh();
        if (onSuccess) onSuccess(null);
      }, 1500);
    } catch (err) {
      if (onError) onError(err.message);
    } finally {
      setBulkOperationLoading(false);
    }
  }, [selectedIds, bulkApproveFeedback, onSuccess, onError, onRefresh]);

  // ---- Bulk reject ----------------------------------------------------------

  const handleBulkRejectClick = useCallback(() => {
    if (selectedIds.size === 0) {
      if (onError) onError('Please select at least one task');
      return;
    }
    setBulkRejectReason('Content quality');
    setBulkRejectFeedback('');
    setBulkAllowRevisions(true);
    setBulkRejectDialogOpen(true);
  }, [selectedIds.size, onError]);

  const handleBulkRejectSubmit = useCallback(async () => {
    if (!bulkRejectFeedback) {
      if (onError) onError('Please provide feedback');
      return;
    }

    setBulkOperationLoading(true);
    if (onError) onError(null);

    try {
      const response = await fetch(`${getApiBaseUrl()}/api/tasks/bulk-reject`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_ids: Array.from(selectedIds),
          reason: bulkRejectReason,
          feedback: bulkRejectFeedback,
          allow_revisions: bulkAllowRevisions,
        }),
      });

      if (!response.ok) {
        throw new Error(`Failed to reject tasks: ${response.statusText}`);
      }

      const result = await response.json();
      if (onSuccess) {
        onSuccess(
          `Bulk rejection completed: ${result.succeeded_count} rejected, ${result.failed_count} failed`
        );
      }
      setBulkRejectDialogOpen(false);
      setSelectedIds(new Set());
      setBulkRejectFeedback('');

      setTimeout(() => {
        if (onRefresh) onRefresh();
        if (onSuccess) onSuccess(null);
      }, 1500);
    } catch (err) {
      if (onError) onError(err.message);
    } finally {
      setBulkOperationLoading(false);
    }
  }, [
    selectedIds,
    bulkRejectReason,
    bulkRejectFeedback,
    bulkAllowRevisions,
    onSuccess,
    onError,
    onRefresh,
  ]);

  return {
    selectedIds,
    bulkApproveDialogOpen,
    setBulkApproveDialogOpen,
    bulkRejectDialogOpen,
    setBulkRejectDialogOpen,
    bulkRejectReason,
    setBulkRejectReason,
    bulkRejectFeedback,
    setBulkRejectFeedback,
    bulkAllowRevisions,
    setBulkAllowRevisions,
    bulkApproveFeedback,
    setBulkApproveFeedback,
    bulkOperationLoading,
    handleToggleSelect,
    handleSelectAll,
    handleClearSelection,
    handleBulkApproveClick,
    handleBulkApproveSubmit,
    handleBulkRejectClick,
    handleBulkRejectSubmit,
  };
};

export default useBulkSelection;
