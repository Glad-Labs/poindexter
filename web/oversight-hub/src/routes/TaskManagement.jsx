/**
 * TaskManagement.jsx - Enhanced Task Management Page
 * Features: Task creation, filtering, sorting, detail view, actions (pause/resume/cancel/delete)
 *
 * Refactored to use:
 * - useFetchTasks hook (eliminates duplicate fetch logic)
 * - statusConfig (centralized status definitions)
 * - formatTaskForDisplay (centralized task formatting)
 */
import React, { useState } from 'react';
import logger from '@/lib/logger';
import useStore from '../store/useStore';
import { useShallow } from 'zustand/react/shallow';
import { bulkUpdateTasks } from '../services/cofounderAgentClient';
import useFetchTasks from '../hooks/useFetchTasks';
import CreateTaskModal from '../components/tasks/CreateTaskModal';
import TaskDetailModal from '../components/tasks/TaskDetailModal';
import TaskFilters from '../components/tasks/TaskFilters';
import { StatusDashboardMetrics } from '../components/tasks/StatusComponents';
import './TaskManagement.css';

function TaskManagement() {
  const { setSelectedTask } = useStore(
    useShallow((s) => ({ setSelectedTask: s.setSelectedTask }))
  );
  const [sortBy, setSortBy] = useState('created_at');
  const [sortDirection, setSortDirection] = useState('desc');
  const [statusFilter, setStatusFilter] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [page, setPage] = useState(1);
  const [limit] = useState(10);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);

  // Use custom hook for task fetching - replaces duplicate fetchTasks/fetchTasksWrapper
  const {
    tasks: localTasks,
    total,
    loading,
    refetch: refreshTasks,
    prependTask,
  } = useFetchTasks(
    page,
    limit,
    10000 // Auto-refresh every 10 seconds
  );

  const normalizeDisplayText = (value) => {
    if (value === null || value === undefined) return null;
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (!trimmed || trimmed.toLowerCase() === 'null') return null;
      return trimmed;
    }
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  };

  // Handler to open detail modal for editing
  const handleEditTask = (task) => {
    setSelectedTask(task);
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

      if (result.updated_count > 0) {
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
      const result = await bulkUpdateTasks([taskId], action);

      if (result.updated_count > 0) {
        setSuccessMessage(`Task ${action} successful`);
        setTimeout(() => setSuccessMessage(null), 3000);
        // Refresh task list
        refreshTasks();
      } else {
        setError(`Failed to ${action} task`);
      }
    } catch (err) {
      logger.error(`Error performing ${action} on task:`, err);
      setError(`Failed to ${action} task: ${err.message}`);
    }
  };

  // Filter and sort tasks
  const getFilteredTasks = () => {
    let filtered = localTasks || [];

    // Apply status filter
    if (statusFilter && statusFilter !== '') {
      filtered = filtered.filter(
        (t) => t.status?.toLowerCase() === statusFilter.toLowerCase()
      );
    }

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

  const handleResetFilters = () => {
    setStatusFilter('');
    setSortBy('created_at');
    setSortDirection('desc');
    setPage(1);
  };

  const filteredTasks = getFilteredTasks();

  return (
    <div className="task-management-container">
      <div className="dashboard-header">
        <h1 className="dashboard-title">Task Management</h1>
      </div>

      {/* Error/Success Messages */}
      {error && (
        <div
          className="alert alert-error"
          role="alert"
          aria-live="assertive"
          style={{ marginBottom: '20px' }}
        >
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            aria-label="Dismiss error"
            style={{ marginLeft: 'auto', cursor: 'pointer' }}
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      )}
      {successMessage && (
        <div
          className="alert alert-success"
          role="status"
          aria-live="polite"
          style={{ marginBottom: '20px' }}
        >
          <span>{successMessage}</span>
        </div>
      )}

      {/* Summary Stats */}
      <div className="summary-stats">
        <div className="stat-box">
          <span className="stat-count">{filteredTasks?.length || 0}</span>
          <span className="stat-label">Filtered Tasks</span>
        </div>
        <div className="stat-box">
          <span className="stat-count">
            {localTasks?.filter((t) => t.status?.toLowerCase() === 'completed')
              .length || 0}
          </span>
          <span className="stat-label">Completed</span>
        </div>
        <div className="stat-box">
          <span className="stat-count">
            {localTasks?.filter((t) => t.status?.toLowerCase() === 'running')
              .length || 0}
          </span>
          <span className="stat-label">Running</span>
        </div>
        <div className="stat-box">
          <span className="stat-count">
            {localTasks?.filter((t) => t.status?.toLowerCase() === 'failed')
              .length || 0}
          </span>
          <span className="stat-label">Failed</span>
        </div>
      </div>

      {/* Metrics Dashboard */}
      <div className="metrics-section" style={{ marginBottom: '30px' }}>
        <StatusDashboardMetrics tasks={localTasks} compact={true} />
      </div>

      {/* Task Filters */}
      <TaskFilters
        sortBy={sortBy}
        sortDirection={sortDirection}
        statusFilter={statusFilter}
        onSortChange={handleSort}
        onDirectionChange={(dir) => setSortDirection(dir)}
        onStatusChange={handleStatusFilter}
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

      {/* Tasks Table */}
      <div className="tasks-table-container">
        {loading && <div className="loading">Loading tasks...</div>}
        {!loading && filteredTasks.length === 0 ? (
          <div className="empty-state">
            <p>
              {statusFilter
                ? 'No tasks found with the selected filter. Try adjusting your filters.'
                : 'No tasks found. Create your first task to get started!'}
            </p>
          </div>
        ) : (
          <>
            <table className="tasks-table">
              <thead>
                <tr>
                  <th
                    onClick={() => handleSort('task_name')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        handleSort('task_name');
                      }
                    }}
                    tabIndex={0}
                    role="columnheader"
                    aria-sort={
                      sortBy === 'task_name'
                        ? sortDirection === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : 'none'
                    }
                    className={`sortable ${sortBy === 'task_name' ? 'active-sort' : ''}`}
                  >
                    Task
                    {sortBy === 'task_name' && (
                      <span aria-hidden="true">
                        {' '}
                        {sortDirection === 'asc' ? '↑' : '↓'}
                      </span>
                    )}
                  </th>
                  <th
                    onClick={() => handleSort('topic')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        handleSort('topic');
                      }
                    }}
                    tabIndex={0}
                    role="columnheader"
                    aria-sort={
                      sortBy === 'topic'
                        ? sortDirection === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : 'none'
                    }
                    className={`sortable ${sortBy === 'topic' ? 'active-sort' : ''}`}
                  >
                    Topic
                    {sortBy === 'topic' && (
                      <span aria-hidden="true">
                        {' '}
                        {sortDirection === 'asc' ? '↑' : '↓'}
                      </span>
                    )}
                  </th>
                  <th
                    onClick={() => handleSort('status')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        handleSort('status');
                      }
                    }}
                    tabIndex={0}
                    role="columnheader"
                    aria-sort={
                      sortBy === 'status'
                        ? sortDirection === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : 'none'
                    }
                    className={`sortable ${sortBy === 'status' ? 'active-sort' : ''}`}
                  >
                    Status
                    {sortBy === 'status' && (
                      <span aria-hidden="true">
                        {' '}
                        {sortDirection === 'asc' ? '↑' : '↓'}
                      </span>
                    )}
                  </th>
                  <th>Progress</th>
                  <th
                    onClick={() => handleSort('created_at')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        handleSort('created_at');
                      }
                    }}
                    tabIndex={0}
                    role="columnheader"
                    aria-sort={
                      sortBy === 'created_at'
                        ? sortDirection === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : 'none'
                    }
                    className={`sortable ${sortBy === 'created_at' ? 'active-sort' : ''}`}
                  >
                    Created
                    {sortBy === 'created_at' && (
                      <span aria-hidden="true">
                        {' '}
                        {sortDirection === 'asc' ? '↑' : '↓'}
                      </span>
                    )}
                  </th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredTasks.map((task) => (
                  <tr
                    key={task.id}
                    className={`status-${task.status?.toLowerCase()} clickable-row`}
                    role="button"
                    tabIndex={0}
                    aria-label={`View details for ${task.task_name || task.topic || 'task'}`}
                    onClick={() => handleEditTask(task)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        handleEditTask(task);
                      }
                    }}
                    title="Click to view details"
                  >
                    <td className="task-name">
                      {normalizeDisplayText(task.task_name) ||
                        normalizeDisplayText(task.topic) ||
                        'Untitled'}
                    </td>
                    <td className="topic">
                      {normalizeDisplayText(task.topic) ||
                        normalizeDisplayText(task.task_name) ||
                        '-'}
                    </td>
                    <td>
                      <span
                        className={`status-badge status-${task.status?.toLowerCase()}`}
                      >
                        {task.status
                          ? task.status.charAt(0).toUpperCase() +
                            task.status.slice(1)
                          : 'Unknown'}
                      </span>
                    </td>
                    <td className="progress">
                      {(() => {
                        const progressValue = task.progress;
                        if (
                          typeof progressValue === 'number' &&
                          progressValue > 0
                        ) {
                          return (
                            <>
                              <div className="progress-bar">
                                <div
                                  className="progress-fill"
                                  style={{ width: `${progressValue}%` }}
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
                        aria-label="View Details"
                        disabled={deleting}
                      >
                        <span aria-hidden="true">👁️</span>
                      </button>
                      {task.status?.toLowerCase() === 'running' && (
                        <>
                          <button
                            className="action-btn pause"
                            onClick={() => handleTaskAction(task.id, 'pause')}
                            title="Pause Task"
                            aria-label="Pause Task"
                            disabled={deleting}
                          >
                            <span aria-hidden="true">⏸️</span>
                          </button>
                          <button
                            className="action-btn cancel"
                            onClick={() => handleTaskAction(task.id, 'cancel')}
                            title="Cancel Task"
                            aria-label="Cancel Task"
                            disabled={deleting}
                          >
                            <span aria-hidden="true">⏹️</span>
                          </button>
                        </>
                      )}
                      {task.status?.toLowerCase() === 'paused' && (
                        <button
                          className="action-btn resume"
                          onClick={() => handleTaskAction(task.id, 'resume')}
                          title="Resume Task"
                          aria-label="Resume Task"
                          disabled={deleting}
                        >
                          <span aria-hidden="true">▶️</span>
                        </button>
                      )}
                      {task.status?.toLowerCase() === 'failed' && (
                        <button
                          className="action-btn retry"
                          onClick={() => handleTaskAction(task.id, 'retry')}
                          title="Retry Task"
                          aria-label="Retry Task"
                          disabled={deleting}
                        >
                          <span aria-hidden="true">🔄</span>
                        </button>
                      )}
                      <button
                        className="action-btn delete"
                        onClick={(e) => handleDeleteTask(e, task.id)}
                        title="Reject Task"
                        aria-label="Reject Task"
                        disabled={deleting}
                      >
                        <span aria-hidden="true">🗑️</span>
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
                    aria-label="Previous page"
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
                            aria-label={`Go to page ${pageNum}`}
                            aria-current={page === pageNum ? 'page' : undefined}
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
                    aria-label="Next page"
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
          }}
          onTaskCreated={(newTask) => {
            setShowCreateModal(false);
            // Show the new task instantly in the table
            if (newTask) {
              prependTask({
                ...newTask,
                task_id: newTask.task_id || newTask.id,
                status: newTask.status || 'pending',
                topic: newTask.topic || newTask.task_name || '',
                created_at: newTask.created_at || new Date().toISOString(),
              });
            }
            // Full refresh from server after 2s to get accurate data
            setTimeout(() => refreshTasks(), 2000);
          }}
        />
      )}

      {/* Task Detail Modal */}
      {showDetailModal && (
        <TaskDetailModal
          onClose={() => {
            setShowDetailModal(false);
            setSelectedTask(null);
            // Refresh task list to reflect any approve/publish/reject actions
            refreshTasks();
          }}
          onUpdate={handleTaskDetailUpdate}
        />
      )}
    </div>
  );
}

export default TaskManagement;
