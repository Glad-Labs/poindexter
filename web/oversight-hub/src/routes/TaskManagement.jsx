import logger from '@/lib/logger';
/**
 * TaskManagement.jsx - Enhanced Task Management Page
 * Features: Task creation, filtering, sorting, detail view, actions (pause/resume/cancel/delete)
 *
 * Refactored to use:
 * - useFetchTasks hook (eliminates duplicate fetch logic)
 * - statusConfig (centralized status definitions)
 * - formatTaskForDisplay (centralized task formatting)
 */
import React, { useState, useEffect, useCallback } from 'react';
import useStore from '../store/useStore';
import {
  bulkUpdateTasks,
  duplicateTask,
} from '../services/cofounderAgentClient';
import { unifiedStatusService } from '../services/unifiedStatusService';
import useFetchTasks from '../hooks/useFetchTasks';
import { useWebSocketEvent } from '../context/WebSocketContext';
import CreateTaskModal from '../components/tasks/CreateTaskModal';
import TaskDetailModal from '../components/tasks/TaskDetailModal';
import TaskFilters from '../components/tasks/TaskFilters';
import './TaskManagement.css';

function TaskManagement() {
  const { setSelectedTask } = useStore();
  const [sortBy, setSortBy] = useState('created_at');
  const [sortDirection, setSortDirection] = useState('desc');
  const [statusFilter, setStatusFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [page, setPage] = useState(1);
  const [limit] = useState(10);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState([]);
  const [bulkActing, setBulkActing] = useState(false);

  // Use custom hook for task fetching - replaces duplicate fetchTasks/fetchTasksWrapper
  const {
    tasks: localTasks,
    total,
    loading,
    refetch: refreshTasks,
  } = useFetchTasks(
    page,
    limit,
    5000, // Auto-refresh every 5 seconds
    { status: statusFilter || undefined, search: searchQuery || undefined }
  );

  // 🔥 NEW: Listen to WebSocket task progress events for real-time updates
  const handleTaskProgressUpdate = useCallback(
    (data) => {
      logger.log('🔔 TaskManagement: Received task progress update:', data);

      // Trigger refetch when task status changes
      // This ensures UI shows tasks in 'in_progress' state immediately
      if (
        data?.status &&
        ['RUNNING', 'COMPLETED', 'FAILED', 'PAUSED'].includes(data.status)
      ) {
        logger.log(
          '🔄 TaskManagement: Triggering task list refresh due to status change'
        );
        refreshTasks();
      }
    },
    [refreshTasks]
  );

  // Subscribe to all task progress events (not just specific task IDs)
  useWebSocketEvent('progress', handleTaskProgressUpdate);

  // Handler to open detail modal for editing
  const handleEditTask = (task) => {
    logger.log('👁️ handleEditTask called with task:', task);
    setSelectedTask(task);
    setSelectedTask(task); // Set in store so TaskDetailModal can access it
    setShowDetailModal(true);
  };

  // Handler to reject a single task (set status to REJECTED instead of deleting)
  const handleDeleteTask = async (event, taskId) => {
    // Prevent the row click from opening the detail modal
    event.stopPropagation();

    if (!window.confirm('Are you sure you want to reject this task?')) {
      return;
    }

    try {
      setDeleting(true);
      setError(null);
      // Use 'reject' action instead of 'delete' to set status to REJECTED
      const result = await bulkUpdateTasks([taskId], 'reject');

      const updatedCount = result?.updated ?? result?.updated_count ?? 0;
      if (updatedCount > 0) {
        setSuccessMessage('Task rejected successfully');
        setTimeout(() => setSuccessMessage(null), 3000);
        // Refresh task list using the hook
        refreshTasks();
      } else {
        setError('Failed to reject task');
      }
    } catch (err) {
      logger.error('Error rejecting task:', err);
      setError(`Failed to reject task: ${err.message}`);
    } finally {
      setDeleting(false);
    }
  };

  // Handler for task detail modal updates
  const handleTaskDetailUpdate = async () => {
    setShowDetailModal(false);
    setSelectedTask(null);
    refreshTasks();
  };

  // Handler for task actions (pause, resume, cancel)
  const handleTaskAction = async (taskId, action) => {
    try {
      setError(null);
      if (action === 'retry') {
        // Use validated status transition endpoint for richer metadata + audit trail.
        await unifiedStatusService.retry(
          taskId,
          'Manual retry from Task Management UI'
        );
        setSuccessMessage('Task queued for retry');
        setTimeout(() => setSuccessMessage(null), 3000);
        refreshTasks();
        return;
      }

      const result = await bulkUpdateTasks([taskId], action);
      const updatedCount = result?.updated ?? result?.updated_count ?? 0;

      if (updatedCount > 0) {
        setSuccessMessage(`Task ${action} successful`);
        setTimeout(() => setSuccessMessage(null), 3000);
        refreshTasks();
      } else {
        setError(`Failed to ${action} task`);
      }
    } catch (err) {
      logger.error(`Error performing ${action} on task:`, err);
      setError(`Failed to ${action} task: ${err.message}`);
    }
  };

  // Selection handlers
  const handleSelectOne = (taskId, checked) => {
    setSelectedTaskIds((prev) =>
      checked ? [...prev, taskId] : prev.filter((id) => id !== taskId)
    );
  };

  const handleSelectAll = (checked) => {
    setSelectedTaskIds(checked ? filteredTasks.map((t) => t.id) : []);
  };

  const handleBulkAction = async (action) => {
    if (selectedTaskIds.length === 0) return;
    const confirmMsg = `${action.charAt(0).toUpperCase() + action.slice(1)} ${selectedTaskIds.length} task(s)?`;
    if (!window.confirm(confirmMsg)) return;

    try {
      setBulkActing(true);
      setError(null);
      const result = await bulkUpdateTasks(selectedTaskIds, action);
      const updatedCount = result?.updated ?? result?.updated_count ?? 0;
      setSuccessMessage(`${updatedCount} task(s) ${action}ed successfully`);
      setTimeout(() => setSuccessMessage(null), 3000);
      setSelectedTaskIds([]);
      refreshTasks();
    } catch (err) {
      logger.error(`Bulk ${action} error:`, err);
      setError(`Bulk ${action} failed: ${err.message}`);
    } finally {
      setBulkActing(false);
    }
  };

  const handleDuplicateTask = async (e, taskId) => {
    e.stopPropagation();
    try {
      setError(null);
      const result = await duplicateTask(taskId);
      setSuccessMessage(`Task duplicated: "${result.task_name}"`);
      setTimeout(() => setSuccessMessage(null), 3000);
      refreshTasks();
    } catch (err) {
      logger.error('Error duplicating task:', err);
      setError(`Failed to duplicate task: ${err.message}`);
    }
  };

  // Sort tasks (filtering is now server-side via API params)
  const getFilteredTasks = () => {
    const filtered = localTasks || [];

    // Apply sorting
    return filtered.sort((a, b) => {
      let aVal = a[sortBy] || 0;
      let bVal = b[sortBy] || 0;

      if (sortBy === 'created_at') {
        aVal = new Date(aVal).getTime();
        bVal = new Date(bVal).getTime();
      }

      if (sortDirection === 'asc') {
        return aVal > bVal ? 1 : -1;
      } else {
        return aVal < bVal ? 1 : -1;
      }
    });
  };

  const handleSort = (column) => {
    if (sortBy === column) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(column);
      setSortDirection('asc');
    }
  };

  const handleStatusFilter = (status) => {
    setStatusFilter(status);
    setPage(1); // Reset to first page
  };

  const handleSearchChange = (query) => {
    setSearchQuery(query);
    setPage(1); // Reset to first page
  };

  const handleResetFilters = () => {
    setStatusFilter('');
    setSearchQuery('');
    setSortBy('created_at');
    setSortDirection('desc');
    setPage(1);
  };

  const getTaskMetadata = (task) => {
    const metadata = task?.task_metadata;
    if (!metadata) return {};

    if (typeof metadata === 'object' && metadata !== null) {
      return metadata;
    }

    if (typeof metadata === 'string') {
      try {
        const parsed = JSON.parse(metadata);
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch {
        return {};
      }
    }

    return {};
  };

  const getStatusClass = (status) =>
    String(status || 'unknown')
      .toLowerCase()
      .replace(/[_\s]+/g, '-');

  const formatStatusLabel = (status) => {
    const normalized = String(status || 'unknown')
      .toLowerCase()
      .replace(/_/g, ' ');

    return normalized
      .split(' ')
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ');
  };

  const formatStepLabel = (step) => {
    if (!step) return '';
    const normalized = String(step)
      .replace(/[_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  };

  const getTaskStepLabel = (task) => {
    const metadata = getTaskMetadata(task);
    const rawStep = metadata.message || metadata.stage || metadata.status;
    if (!rawStep) return '';

    const status = String(task?.status || '').toLowerCase();
    if (
      status !== 'pending' &&
      status !== 'in_progress' &&
      status !== 'running'
    ) {
      return '';
    }

    return formatStepLabel(rawStep);
  };

  const getRetryCount = (task) => {
    const metadata = getTaskMetadata(task);
    return Number(metadata.retry_count || 0);
  };

  const filteredTasks = getFilteredTasks();

  return (
    <div className="task-management-container">
      <div className="dashboard-header">
        <h1 className="dashboard-title">Task Management</h1>
      </div>

      {/* Error/Success Messages */}
      {error && (
        <div className="alert alert-error" style={{ marginBottom: '20px' }}>
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            style={{ marginLeft: 'auto', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>
      )}
      {successMessage && (
        <div className="alert alert-success" style={{ marginBottom: '20px' }}>
          <span>{successMessage}</span>
        </div>
      )}

      {/* Task Filters */}
      <TaskFilters
        sortBy={sortBy}
        sortDirection={sortDirection}
        statusFilter={statusFilter}
        searchQuery={searchQuery}
        onSortChange={handleSort}
        onDirectionChange={(dir) => setSortDirection(dir)}
        onStatusChange={handleStatusFilter}
        onSearchChange={handleSearchChange}
        onResetFilters={handleResetFilters}
      />

      {/* Create Task Button */}
      <div className="table-controls">
        <div className="button-group">
          <button
            className="btn-create-task"
            onClick={() => setShowCreateModal(true)}
          >
            ➕ Create Task
          </button>
          <button
            className="btn-refresh"
            onClick={refreshTasks}
            disabled={loading}
            title="Refresh task list"
          >
            🔄 Refresh
          </button>
          <button
            className="btn-clear-filters"
            onClick={handleResetFilters}
            title="Clear all filters and sorts"
          >
            ✕ Clear Filters
          </button>
        </div>

        <div className="stats-line">
          <span className="stat-text">
            {filteredTasks.length} of {total} tasks
          </span>
        </div>
      </div>

      {/* Bulk Action Toolbar */}
      {selectedTaskIds.length > 0 && (
        <div className="bulk-action-bar">
          <span className="bulk-count">{selectedTaskIds.length} selected</span>
          <button
            className="action-btn pause"
            onClick={() => handleBulkAction('pause')}
            disabled={bulkActing}
            title="Pause selected tasks"
          >
            ⏸️ Pause
          </button>
          <button
            className="action-btn resume"
            onClick={() => handleBulkAction('resume')}
            disabled={bulkActing}
            title="Resume selected tasks"
          >
            ▶️ Resume
          </button>
          <button
            className="action-btn cancel"
            onClick={() => handleBulkAction('cancel')}
            disabled={bulkActing}
            title="Cancel selected tasks"
          >
            ⏹️ Cancel
          </button>
          <button
            className="action-btn delete"
            onClick={() => handleBulkAction('reject')}
            disabled={bulkActing}
            title="Reject selected tasks"
          >
            🗑️ Reject
          </button>
          <button
            className="btn-clear-filters"
            onClick={() => setSelectedTaskIds([])}
            title="Clear selection"
          >
            ✕ Clear
          </button>
        </div>
      )}

      {/* Tasks Table */}
      <div className="tasks-table-container">
        {loading && <div className="loading">Loading tasks...</div>}
        {!loading && filteredTasks.length === 0 ? (
          <div className="empty-state">
            {(statusFilter || searchQuery) ? (
              <>
                <p>
                  No tasks match
                  {searchQuery ? ` "${searchQuery}"` : ''}
                  {statusFilter ? ` with status "${formatStatusLabel(statusFilter)}"` : ''}.
                </p>
                <button
                  className="btn-clear-filters"
                  onClick={handleResetFilters}
                >
                  Clear filters
                </button>
              </>
            ) : (
              <>
                <p>No tasks yet. Create your first task to get started!</p>
                <button
                  className="btn-create-task"
                  onClick={() => setShowCreateModal(true)}
                >
                  Create Task
                </button>
              </>
            )}
          </div>
        ) : (
          <>
            <table className="tasks-table">
              <thead>
                <tr>
                  <th className="checkbox-col">
                    <input
                      type="checkbox"
                      aria-label="Select all tasks"
                      checked={
                        filteredTasks.length > 0 &&
                        filteredTasks.every((t) =>
                          selectedTaskIds.includes(t.id)
                        )
                      }
                      onChange={(e) => handleSelectAll(e.target.checked)}
                    />
                  </th>
                  <th
                    onClick={() => handleSort('task_name')}
                    className={`sortable ${sortBy === 'task_name' ? 'active-sort' : ''}`}
                  >
                    Task{' '}
                    {sortBy === 'task_name' &&
                      (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    onClick={() => handleSort('topic')}
                    className={`sortable ${sortBy === 'topic' ? 'active-sort' : ''}`}
                  >
                    Topic{' '}
                    {sortBy === 'topic' &&
                      (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th
                    onClick={() => handleSort('status')}
                    className={`sortable ${sortBy === 'status' ? 'active-sort' : ''}`}
                  >
                    Status{' '}
                    {sortBy === 'status' &&
                      (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th>Progress</th>
                  <th
                    onClick={() => handleSort('created_at')}
                    className={`sortable ${sortBy === 'created_at' ? 'active-sort' : ''}`}
                  >
                    Created{' '}
                    {sortBy === 'created_at' &&
                      (sortDirection === 'asc' ? '↑' : '↓')}
                  </th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredTasks.map((task) => (
                  <tr
                    key={task.id}
                    className={`status-${getStatusClass(task.status)} clickable-row${selectedTaskIds.includes(task.id) ? ' row-selected' : ''}`}
                    onClick={() => handleEditTask(task)}
                    title="Click to view details"
                  >
                    <td
                      className="checkbox-col"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        aria-label={`Select task ${task.id}`}
                        checked={selectedTaskIds.includes(task.id)}
                        onChange={(e) =>
                          handleSelectOne(task.id, e.target.checked)
                        }
                      />
                    </td>
                    <td className="task-name">
                      {typeof task.task_name === 'object'
                        ? JSON.stringify(task.task_name)
                        : task.task_name || task.topic || 'Untitled'}
                    </td>
                    <td className="topic">
                      {typeof task.topic === 'object'
                        ? JSON.stringify(task.topic)
                        : task.topic || task.task_name || '-'}
                    </td>
                    <td>
                      <div className="status-cell">
                        <span
                          className={`status-badge status-${getStatusClass(task.status)}`}
                        >
                          {formatStatusLabel(task.status)}
                        </span>
                        {getRetryCount(task) > 0 && (
                          <span
                            className="retry-count-badge"
                            title={`Retry attempts: ${getRetryCount(task)}`}
                          >
                            Retry #{getRetryCount(task)}
                          </span>
                        )}
                        {getTaskStepLabel(task) && (
                          <div
                            className="status-step-text"
                            title={getTaskStepLabel(task)}
                          >
                            {getTaskStepLabel(task)}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="progress">
                      {(() => {
                        const metadata = getTaskMetadata(task);
                        const progressValue =
                          typeof metadata.percentage === 'number'
                            ? metadata.percentage
                            : task.progress || 0;
                        const stage = metadata.stage || metadata.status || '';
                        const isActive = [
                          'pending',
                          'in_progress',
                          'running',
                        ].includes(String(task.status || '').toLowerCase());

                        if (
                          typeof progressValue === 'number' &&
                          progressValue > 0
                        ) {
                          return (
                            <>
                              <div className="progress-bar">
                                <div
                                  className={`progress-fill ${isActive ? 'active' : ''}`}
                                  data-stage={stage}
                                  style={{ width: `${progressValue}%` }}
                                  title={stage ? `Stage: ${stage}` : ''}
                                />
                              </div>
                              <span className="progress-text">
                                {progressValue}%
                              </span>
                            </>
                          );
                        }
                        return <span className="progress-text">-</span>;
                      })()}
                    </td>
                    <td className="task-date">
                      {(() => {
                        try {
                          if (task.created_at) {
                            const dateObj = new Date(task.created_at);
                            if (isNaN(dateObj.getTime())) return '-';
                            return dateObj.toLocaleDateString('en-US', {
                              month: 'short',
                              day: 'numeric',
                              year: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            });
                          }
                          return '-';
                        } catch {
                          return '-';
                        }
                      })()}
                    </td>
                    <td className="actions">
                      <button
                        className="action-btn view"
                        onClick={() => handleEditTask(task)}
                        title="View Details"
                        disabled={deleting}
                      >
                        👁️
                      </button>
                      {(task.status?.toLowerCase() === 'running' ||
                        task.status?.toLowerCase() === 'in_progress') && (
                        <>
                          <button
                            className="action-btn pause"
                            onClick={() => handleTaskAction(task.id, 'pause')}
                            title="Pause Task"
                            disabled={deleting}
                          >
                            ⏸️
                          </button>
                          <button
                            className="action-btn cancel"
                            onClick={() => handleTaskAction(task.id, 'cancel')}
                            title="Cancel Task"
                            disabled={deleting}
                          >
                            ⏹️
                          </button>
                        </>
                      )}
                      {(task.status?.toLowerCase() === 'paused' ||
                        task.status?.toLowerCase() === 'on_hold') && (
                        <button
                          className="action-btn resume"
                          onClick={() => handleTaskAction(task.id, 'resume')}
                          title="Resume Task"
                          disabled={deleting}
                        >
                          ▶️
                        </button>
                      )}
                      {task.status?.toLowerCase() === 'failed' && (
                        <button
                          className="action-btn retry"
                          onClick={() => handleTaskAction(task.id, 'retry')}
                          title="Retry Task"
                          disabled={deleting}
                        >
                          🔄
                        </button>
                      )}
                      <button
                        className="action-btn duplicate"
                        onClick={(e) => handleDuplicateTask(e, task.id)}
                        title="Duplicate Task"
                        disabled={deleting}
                      >
                        📋
                      </button>
                      <button
                        className="action-btn delete"
                        onClick={(e) => handleDeleteTask(e, task.id)}
                        title="Reject Task"
                        disabled={deleting}
                      >
                        🗑️
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Pagination Controls */}
            {total > limit && (
              <div className="pagination-container">
                <div className="pagination-info">
                  Showing {Math.min((page - 1) * limit + 1, total)}-
                  {Math.min(page * limit, total)} of {total} tasks
                </div>

                <div className="pagination-controls">
                  <button
                    onClick={() => setPage(page - 1)}
                    disabled={page === 1}
                    className="pagination-btn"
                    title="Previous page"
                  >
                    ← Previous
                  </button>

                  <div className="pagination-pages">
                    {Array.from(
                      { length: Math.min(Math.ceil(total / limit), 5) },
                      (_, i) => {
                        const totalPages = Math.ceil(total / limit);
                        let pageNum;
                        if (totalPages <= 5) {
                          pageNum = i + 1;
                        } else if (page <= 3) {
                          pageNum = i + 1;
                        } else if (page > totalPages - 3) {
                          pageNum = totalPages - 4 + i;
                        } else {
                          pageNum = page - 2 + i;
                        }

                        return (
                          <button
                            key={pageNum}
                            onClick={() => setPage(pageNum)}
                            className={`page-btn ${
                              page === pageNum ? 'active' : ''
                            }`}
                            title={`Go to page ${pageNum}`}
                          >
                            {pageNum}
                          </button>
                        );
                      }
                    )}
                    {Math.ceil(total / limit) > 5 &&
                      page < Math.ceil(total / limit) - 2 && (
                        <span className="pagination-dots">...</span>
                      )}
                  </div>

                  <button
                    onClick={() => setPage(page + 1)}
                    disabled={page === Math.ceil(total / limit)}
                    className="pagination-btn"
                    title="Next page"
                  >
                    Next →
                  </button>
                </div>

                <div className="pagination-page-info">
                  Page {page} of {Math.ceil(total / limit)}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Create Task Modal */}
      {showCreateModal && (
        <CreateTaskModal
          isOpen={showCreateModal}
          onClose={() => {
            setShowCreateModal(false);
            refreshTasks();
          }}
          onTaskCreated={() => {
            setShowCreateModal(false);
            refreshTasks();
          }}
        />
      )}

      {/* Task Detail Modal */}
      {showDetailModal && (
        <TaskDetailModal
          onClose={() => {
            setShowDetailModal(false);
            setSelectedTask(null);
          }}
          onUpdate={handleTaskDetailUpdate}
        />
      )}
    </div>
  );
}

export default TaskManagement;
