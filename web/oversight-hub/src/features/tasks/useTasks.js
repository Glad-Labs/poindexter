import logger from '@/lib/logger';
import { useState, useEffect } from 'react';
import axios from 'axios';
import useStore from '../../store/useStore';
import { getAuthToken } from '../../services/authService';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const useTasks = (page = 1, limit = 10) => {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const setStoreTasks = useStore((state) => state.setTasks);

  useEffect(() => {
    let isMounted = true;
    let retryCount = 0;
    const maxRetries = 2; // Reduced from 3 to speed up failure detection
    let loadingTimeout = null;

    const fetchTasks = async () => {
      try {
        setLoading(true);
        const token = getAuthToken();
        const headers = { 'Content-Type': 'application/json' };
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }

        // Set a timeout for the overall loading process (10 seconds max)
        // This prevents the UI from hanging indefinitely
        loadingTimeout = setTimeout(() => {
          if (isMounted) {
            logger.warn('⏱️ Tasks fetch timeout - taking too long');
            setError(
              'Tasks fetch timeout - backend may not be responding. Check http://localhost:8000/docs'
            );
            setLoading(false);
          }
        }, 10000);

        // Calculate offset from page number
        const offset = (page - 1) * limit;

        const response = await axios.get(`${API_URL}/api/tasks`, {
          timeout: 8000, // Reduced from 120000 (2 min) to 8 seconds per request
          headers,
          params: {
            offset,
            limit,
          },
        });

        if (loadingTimeout) {
          clearTimeout(loadingTimeout);
        }

        if (isMounted) {
          // Handle TaskListResponse format with pagination info
          let tasksData = [];
          let totalCount = 0;

          if (response.data.tasks) {
            // New format: { tasks: [], total: 100, offset: 0, limit: 10 }
            tasksData = response.data.tasks;
            totalCount = response.data.total || 0;
          } else if (Array.isArray(response.data)) {
            // Old format: just array
            tasksData = response.data;
            totalCount = response.data.length;
          } else if (response.data.results) {
            tasksData = response.data.results;
            totalCount = response.data.count || response.data.results.length;
          } else if (response.data.data) {
            tasksData = response.data.data;
            totalCount = response.data.total || response.data.data.length;
          }

          setTasks(tasksData);
          setTotal(totalCount);
          setHasMore(offset + tasksData.length < totalCount);
          setStoreTasks(tasksData);
          setError(null);
          setLoading(false);
        }
      } catch (err) {
        if (loadingTimeout) {
          clearTimeout(loadingTimeout);
        }

        if (isMounted) {
          logger.error('❌ Error fetching tasks:', err.message, {
            retryCount,
            maxRetries,
          });

          // Check if this is an auth error
          if (err.response?.status === 401) {
            setError('Not authenticated. Please login first.');
            setLoading(false);
            return;
          }

          // Retry logic for non-auth errors
          if (retryCount < maxRetries && err.code !== 'ECONNABORTED') {
            retryCount++;
            const retryDelay = 1000 * retryCount;
            logger.log(
              `⏳ Retrying tasks fetch in ${retryDelay}ms (attempt ${retryCount}/${maxRetries})`
            );
            setTimeout(fetchTasks, retryDelay);
          } else {
            setError(`Failed to fetch tasks: ${err.message}`);
            setLoading(false);
          }
        }
      }
    };

    fetchTasks();

    return () => {
      isMounted = false;
      if (loadingTimeout) {
        clearTimeout(loadingTimeout);
      }
    };
  }, [page, limit, setStoreTasks]);

  return { tasks, loading, error, total, hasMore, page, limit };
};
