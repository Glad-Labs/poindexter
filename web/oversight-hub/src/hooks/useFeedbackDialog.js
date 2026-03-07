import logger from '@/lib/logger';
/**
 * useFeedbackDialog Hook
 *
 * Manages approval/rejection dialog state and callbacks.
 * Extracted to provide:
 * - Dialog open/close state management
 * - Approval/rejection handling
 * - Loading state during submission
 * - Error message display
 *
 * Previously duplicated in Result and Error message components (~45 lines each)
 */

import { useState, useCallback } from 'react';

/**
 * Hook for managing approval/rejection feedback dialog
 *
 * @param {function} onApprove - Callback when user approves
 * @param {function} onReject - Callback when user rejects
 * @param {function} onClose - Optional callback when dialog closes
 *
 * @returns {object} Contains:
 *   - isOpen: whether dialog is open
 *   - open: function to open dialog
 *   - close: function to close dialog
 *   - isSubmitting: whether feedback is being submitted
 *   - error: error message if submission failed
 *   - approve: function to submit approval
 *   - reject: function to submit rejection
 *   - reset: function to reset all state
 *
 * @example
 * const { isOpen, open, close, approve, reject, isSubmitting, error } = useFeedbackDialog(
 *   (feedback) => logger.log('Approved:', feedback),
 *   (feedback) => logger.log('Rejected:', feedback)
 * );
 *
 * return (
 *   <>
 *     <button onClick={open}>Request Feedback</button>
 *     {isOpen && (
 *       <Dialog open={isOpen} onClose={close}>
 *         <button onClick={() => approve('Looking good!')} disabled={isSubmitting}>
 *           Approve
 *         </button>
 *         <button onClick={() => reject('Needs revision')} disabled={isSubmitting}>
 *           Reject
 *         </button>
 *       </Dialog>
 *     )}
 *     {error && <Alert severity="error">{error}</Alert>}
 *   </>
 * );
 */
export const useFeedbackDialog = (onApprove, onReject, onClose) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const open = useCallback(() => {
    setIsOpen(true);
    setError(null);
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
    setError(null);
    if (onClose) {
      onClose();
    }
  }, [onClose]);

  const approve = useCallback(
    async (feedback) => {
      setIsSubmitting(true);
      setError(null);

      try {
        if (onApprove) {
          await onApprove(feedback);
        }
        close();
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to approve';
        setError(errorMessage);
      } finally {
        setIsSubmitting(false);
      }
    },
    [onApprove, close]
  );

  const reject = useCallback(
    async (feedback) => {
      setIsSubmitting(true);
      setError(null);

      try {
        if (onReject) {
          await onReject(feedback);
        }
        close();
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to reject';
        setError(errorMessage);
      } finally {
        setIsSubmitting(false);
      }
    },
    [onReject, close]
  );

  const reset = useCallback(() => {
    setIsOpen(false);
    setIsSubmitting(false);
    setError(null);
  }, []);

  return {
    isOpen,
    open,
    close,
    isSubmitting,
    error,
    approve,
    reject,
    reset,
  };
};

export default useFeedbackDialog;
