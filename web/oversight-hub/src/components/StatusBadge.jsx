import React from 'react';

const StatusBadge = ({ status }) => {
  const baseClasses = 'px-2 py-1 text-xs font-semibold rounded-full';
  switch (status.toLowerCase()) {
    case 'completed':
      return (
        <span className={`${baseClasses} bg-green-600 text-white`}>
          Completed
        </span>
      );
    case 'in_progress':
      return (
        <span className={`${baseClasses} bg-yellow-500 text-yellow-900`}>
          In Progress
        </span>
      );
    case 'queued':
      return (
        <span className={`${baseClasses} bg-blue-700 text-white`}>Queued</span>
      );
    case 'failed':
      return (
        <span className={`${baseClasses} bg-red-700 text-white`}>Failed</span>
      );
    default:
      return (
        <span className={`${baseClasses} bg-gray-600 text-gray-200`}>
          {status}
        </span>
      );
  }
};

export default StatusBadge;
