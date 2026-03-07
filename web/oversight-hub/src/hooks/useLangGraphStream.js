import logger from '@/lib/logger';
import { useState, useEffect } from 'react';
import { getWebSocketUrl } from '../config/apiConfig';

/**
 * Hook for streaming blog creation progress from LangGraph
 *
 * Usage:
 *   const progress = useLangGraphStream(requestId);
 *   if (progress.status === 'error') return <ErrorMessage error={progress.error} />;
 *   return <ProgressStepper phases={progress.phases} currentPhase={progress.phase} />;
 */
export function useLangGraphStream(requestId) {
  const [progress, setProgress] = useState({
    phase: 'pending',
    progress: 0,
    status: 'waiting',
    content: '',
    quality: 0,
    refinements: 0,
    error: null,
    phases: [
      { name: 'Research', completed: false },
      { name: 'Outline', completed: false },
      { name: 'Draft', completed: false },
      { name: 'Quality Check', completed: false },
      { name: 'Finalization', completed: false },
    ],
  });

  useEffect(() => {
    if (!requestId) {
      return;
    }

    // Get WebSocket URL from validated config
    const wsBaseUrl = getWebSocketUrl();
    const wsUrl = `${wsBaseUrl}/api/content/langgraph/ws/blog-posts/${requestId}`;

    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      logger.log('LangGraph WebSocket connected:', requestId);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'progress') {
          const phaseIndex = getPhaseIndex(data.node);
          setProgress((prev) => ({
            ...prev,
            phase: data.node,
            progress: data.progress,
            status: 'in_progress',
            content: data.current_content_preview || prev.content,
            quality: data.quality_score || prev.quality,
            refinements: data.refinement_count || prev.refinements,
            phases: prev.phases.map((p, i) => ({
              ...p,
              completed: i < phaseIndex,
            })),
          }));
        } else if (data.type === 'complete') {
          setProgress((prev) => ({
            ...prev,
            phase: 'complete',
            progress: 100,
            status: 'completed',
            phases: prev.phases.map((p) => ({ ...p, completed: true })),
          }));
        } else if (data.type === 'error') {
          setProgress((prev) => ({
            ...prev,
            status: 'error',
            error: data.error,
          }));
        }
      } catch (parseError) {
        logger.error('Failed to parse LangGraph message:', parseError);
        setProgress((prev) => ({
          ...prev,
          status: 'error',
          error: 'Failed to parse server response',
        }));
      }
    };

    ws.onerror = (error) => {
      logger.error('LangGraph WebSocket error:', error);
      setProgress((prev) => ({
        ...prev,
        status: 'error',
        error: 'Connection failed',
      }));
    };

    ws.onclose = () => {
      logger.log('LangGraph WebSocket disconnected');
    };

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  };, [requestId]);

  return progress;
}

function getPhaseIndex(phase) {
  const map = {
    research: 0,
    outline: 1,
    draft: 2,
    assess: 3,
    finalize: 4,
  };
  return map[phase] || 0;
}
