import logger from '@/lib/logger';
/**
 * useTaskData - Custom hook for task data fetching and management
 *
 * Handles:
 * - Fetching tasks from backend with pagination
 * - Managing task list state with useReducer
 * - Memoized pagination calculation
 * - KPI calculation from all tasks
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useReducer,
  useMemo,
} from 'react';
import { getTasks } from '../services/taskService';

/**
 * Reducer for managing task data state
 */
const taskDataReducer = (state, action) => {
  switch (action.type) {
    case 'FETCH_START':
      return { ...state, loading: true, isFetching: true, error: null };

    case 'FETCH_SUCCESS':
      return {
        ...state,
        allTasks: action.payload,
        total: action.payload.length,
        loading: false,
        isFetching: false,
        error: null,
      };

    case 'FETCH_ERROR':
      return {
        ...state,
        loading: false,
        isFetching: false,
        error: action.payload,
      };

    case 'SET_TASKS':
      return { ...state, tasks: action.payload };

    case 'SET_ALL_TASKS':
      return {
        ...state,
        allTasks: action.payload,
        total: action.payload.length,
      };

    default:
      return state;
  }
};

export function useTaskData(
  page = 1,
  limit = 10,
  sortBy = 'created_at',
  sortDirection = 'desc'
) {
  // Centralized state management with useReducer
  const [state, dispatch] = useReducer(taskDataReducer, {
    tasks: [],
    allTasks: [],
    total: 0,
    loading: true,
    error: null,
    isFetching: false,
  });

  const isFetchingRef = useRef(false); // Prevent concurrent requests

  const fetchTasks = useCallback(async () => {
    // Guard: prevent concurrent requests
    if (isFetchingRef.current) {
      return;
    }

    try {
      dispatch({ type: 'FETCH_START' });
      isFetchingRef.current = true;

      // Fetch ALL tasks first (with high limit) for accurate KPI stats
      const allTasksData = await getTasks(0, 1000, {
        sortBy,
        sortDirection,
      });

      const fetchedAllTasks = allTasksData || [];
      dispatch({
        type: 'FETCH_SUCCESS',
        payload: fetchedAllTasks,
      });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      dispatch({
        type: 'FETCH_ERROR',
        payload: `Unable to load tasks: ${errorMessage}`,
      });
      logger.error('Failed to fetch tasks:', err);
    } finally {
      isFetchingRef.current = false;
    }
  }, [sortBy, sortDirection]);

  // Memoized pagination calculation - only recalculates when dependencies change
  const paginatedTasks = useMemo(() => {
    const offset = (page - 1) * limit;
    return state.allTasks.slice(offset, offset + limit);
  }, [state.allTasks, page, limit]);

  // Update tasks when pagination changes
  useEffect(() => {
    dispatch({ type: 'SET_TASKS', payload: paginatedTasks });
  }, [paginatedTasks]);

  // Fetch on mount and when dependencies change
  useEffect(() => {
    fetchTasks();
  }, [fetchTasks, sortBy, sortDirection]);

  // Note: Auto-refresh disabled (was causing modal scrolling)
  // Users can manually refresh with the Refresh button
  // useEffect(() => {
  //   const interval = setInterval(() => {
  //     fetchTasks();
  //   }, 30000);
  //   return () => clearInterval(interval);
  // }, [fetchTasks]);

  return {
    tasks: state.tasks,
    allTasks: state.allTasks,
    total: state.total,
    loading: state.loading,
    error: state.error,
    isFetching: state.isFetching,
    fetchTasks,
    setTasks: (tasks) => dispatch({ type: 'SET_TASKS', payload: tasks }),
    setAllTasks: (allTasks) =>
      dispatch({ type: 'SET_ALL_TASKS', payload: allTasks }),
  };
}
