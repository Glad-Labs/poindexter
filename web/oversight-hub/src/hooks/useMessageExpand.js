import logger from '@/lib/logger';
/**
 * useMessageExpand Hook
 *
 * Manages expand/collapse state for message cards.
 * Extracted to provide:
 * - Reusable expand/collapse logic
 * - Callback triggers for parent components
 * - Animation state tracking
 * - Accessibility support
 *
 * Previously duplicated across all 4 message components (~40 lines each)
 */

import { useState, useCallback } from 'react';

/**
 * Hook for managing message expand/collapse state
 *
 * @param {boolean} defaultOpen - Initial state (default: false)
 * @param {function} onExpand - Callback when expanded
 * @param {function} onCollapse - Callback when collapsed
 * @returns {object} Contains:
 *   - expanded: current expand state
 *   - toggle: function to toggle state
 *   - setExpanded: function to set state directly
 *   - handleToggle: function with callbacks
 *
 * @example
 * const { expanded, toggle, handleToggle } = useMessageExpand(false, () => logger.log('Expanded'), () => logger.log('Collapsed'));
 * return (
 *   <>
 *     <button onClick={handleToggle}>
 *       {expanded ? 'Show Less' : 'Show More'}
 *     </button>
 *     {expanded && <Details />}
 *   </>
 * );
 */
export const useMessageExpand = (defaultOpen = false, onExpand, onCollapse) => {
  const [expanded, setExpanded] = useState(defaultOpen);

  // Simple toggle without callbacks
  const toggle = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  // Toggle with callbacks for parent components
  const handleToggle = useCallback(() => {
    setExpanded((prev) => {
      const newState = !prev;
      if (newState && onExpand) {
        onExpand();
      } else if (!newState && onCollapse) {
        onCollapse();
      }
      return newState;
    });
  }, [onExpand, onCollapse]);

  // Controlled setter
  const setExpandedControlled = useCallback(
    (value) => {
      setExpanded(value);
      if (value && onExpand) {
        onExpand();
      } else if (!value && onCollapse) {
        onCollapse();
      }
    },
    [onExpand, onCollapse]
  );

  return {
    expanded,
    toggle,
    setExpanded: setExpandedControlled,
    handleToggle,
  };
};

export default useMessageExpand;
