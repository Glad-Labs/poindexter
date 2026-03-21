import logger from '@/lib/logger';
/**
 * useFetchTasks - Custom hook for task fetching with auto-refresh
 *
 * Eliminates duplication between TaskManagement component's:
 * - fetchTasksWrapper (in useEffect)
 * - fetchTasks (standalone function)
 *
 * Features:
 * - Centralized task fetching logic
 * - Auto-refresh every 30 seconds
 * - Consistent error handling
 * - Proper loading state management
 * - Integration with Zustand store
 */

import { useState, useCallback, useEffect } from 'react';
import useStore from '../store/useStore';
import { getTasks } from '../services/cofounderAgentClient';

/**
 * Hook for fetching tasks with pagination and auto-refresh
 *
 * @param {number} page - Current page number (1-indexed)
 * @param {number} limit - Items per page
 * @param {number} autoRefreshInterval - Auto-refresh interval in ms (0 = disabled, default 30000)
 * @returns {object} { tasks, total, loading, error, refetch }
 */
export const useFetchTasks = (
  page = 1,
  limit = 10,
  autoRefreshInterval = 30000,
  { status, search } = {}
) => {
  const [tasks, setTasks] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Apply an optimistic in-place update to a single task by id.
  // Avoids a full API re-fetch for intermediate state changes (e.g. RUNNING, PAUSED).
  const updateTask = useCallback((taskId, patch) => {
    setTasks((prev) =>
      prev.map((t) =>
        t.id === taskId || t.task_id === taskId ? { ...t, ...patch } : t
      )
    );
  }, []);
  const setStoreTasks = useStore((state) => state.setTasks);
  const isAuthenticated = useStore((state) => state.isAuthenticated);
  const authInitialized = useStore((state) => state.authInitialized);

  // Core fetch function
  const fetchTasks = useCallback(async () => {
    if (!authInitialized || !isAuthenticated) {
      setLoading(false);
      setError(null);
      setTasks([]);
      setTotal(0);
      setStoreTasks([]);
      return;
    }

    try {
      setLoading(true);
      setError(null);

      logger.log('🔵 useFetchTasks: Fetching tasks...');
      const offset = (page - 1) * limit;

      try {
        const response = await getTasks(limit, offset, { status, search });
        logger.log('🟢 useFetchTasks: Response received:', response);

        // Handle success
        if (response && response.success !== false) {
          // API returns { tasks: [...], total: 74, offset: 0, limit: 10 }
          const tasksData = response.tasks || response.data || [];
          const totalCount = response.total || response.pagination?.total || 0;

          logger.log(
            `✅ useFetchTasks: Parsed ${tasksData.length} tasks out of ${totalCount} total`
          );
          setTasks(tasksData);
          setTotal(totalCount);
          setStoreTasks(tasksData);
          return;
        }
      } catch (apiError) {
        logger.error(
          '🔴 useFetchTasks: API error - displaying error to user',
          apiError.message
        );
        setError(`Failed to fetch tasks: ${apiError.message}`);
        setTasks([]);
        setTotal(0);
        setStoreTasks([]);
        return;
      }
    } catch (err) {
      logger.error('🔴 useFetchTasks: Unexpected error:', err);
      setError(err.message || 'Failed to fetch tasks');
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [
    page,
    limit,
    status,
    search,
    setStoreTasks,
    isAuthenticated,
    authInitialized,
  ]);

  // Auto-refresh effect
  useEffect(() => {
    if (!authInitialized || !isAuthenticated) {
      return;
    }

    // Fetch immediately on mount or when page/limit changes
    fetchTasks();

    // Set up auto-refresh if interval > 0
    if (autoRefreshInterval > 0) {
      const interval = setInterval(fetchTasks, autoRefreshInterval);
      return () => clearInterval(interval);
    }
  }, [fetchTasks, autoRefreshInterval, isAuthenticated, authInitialized]);

  // Optimistically prepend a newly created task so it appears instantly
  const prependTask = useCallback((newTask) => {
    setTasks((prev) => [newTask, ...prev]);
    setTotal((prev) => prev + 1);
  }, []);

  return {
    tasks,
    total,
    loading,
    error,
    refetch: fetchTasks,
    updateTask,
    prependTask,
  };
};

export default useFetchTasks;
