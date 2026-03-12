/**
 * TaskTable - Task list table with selection and actions
 *
 * Displays:
 * - Task name, status, type, created date
 * - Checkbox selection for bulk actions
 * - Action buttons (view, edit, delete)
 * - Pagination controls
 */

import React from 'react';
import PropTypes from 'prop-types';
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Checkbox,
  IconButton,
  Tooltip,
  TablePagination,
  CircularProgress,
  Chip,
} from '@mui/material';
import {
  Edit as EditIcon,
  Delete as DeleteIcon,
  Visibility as ViewIcon,
  Pause as PauseIcon,
  PlayArrow as PlayIcon,
  Stop as StopIcon,
} from '@mui/icons-material';
import { getStatusColor } from '../../lib/statusConfig';
import { getStatusLabel } from '../../Constants/statusEnums';
import { tableHeaderRow, loadingContainer } from '../../lib/muiStyles';

const TaskTable = ({
  tasks = [],
  loading = false,
  page = 1,
  limit = 10,
  total = 0,
  selectedTasks = [],
  onSelectTask,
  onSelectAll,
  onSelectOne,
  onPageChange,
  onRowsPerPageChange,
  onEditTask,
  onDeleteTask,
  onPauseTask,
  onResumeTask,
  onCancelTask,
}) => {
  if (loading && tasks.length === 0) {
    return (
      <Box sx={loadingContainer} role="status" aria-label="Loading tasks">
        <CircularProgress aria-label="Loading tasks" />
      </Box>
    );
  }

  const isAllSelected =
    tasks.length > 0 && selectedTasks.length === tasks.length;
  const isIndeterminate =
    selectedTasks.length > 0 && selectedTasks.length < tasks.length;

  return (
    <Box>
      <TableContainer component={Paper} sx={{ mb: 2 }}>
        <Table>
          <TableHead>
            <TableRow sx={tableHeaderRow}>
              <TableCell padding="checkbox">
                <Checkbox
                  indeterminate={isIndeterminate}
                  checked={isAllSelected}
                  onChange={(e) => onSelectAll(e.target.checked)}
                  inputProps={{ 'aria-label': 'Select all tasks' }}
                />
              </TableCell>
              <TableCell>Name</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Created</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {tasks.map((task) => (
              <TableRow
                key={task.id}
                selected={selectedTasks.includes(task.id)}
                hover
              >
                <TableCell padding="checkbox">
                  <Checkbox
                    checked={selectedTasks.includes(task.id)}
                    onChange={(e) => onSelectOne(task.id, e.target.checked)}
                    inputProps={{
                      'aria-label': `Select task ${task.task_name || task.topic || 'Untitled'}`,
                    }}
                  />
                </TableCell>
                <TableCell>
                  <Tooltip title={task.task_name || task.topic || 'Untitled'}>
                    <span>
                      {(task.task_name || task.topic || 'Untitled').substring(
                        0,
                        40
                      )}
                    </span>
                  </Tooltip>
                </TableCell>
                <TableCell>
                  <Chip
                    label={task.task_type || 'unknown'}
                    size="small"
                    variant="outlined"
                  />
                </TableCell>
                <TableCell>
                  <Chip
                    label={getStatusLabel(task.status)}
                    size="small"
                    color={getStatusColor(task.status)}
                    variant="filled"
                  />
                </TableCell>
                <TableCell>
                  {new Date(task.created_at).toLocaleDateString()} at{' '}
                  {new Date(task.created_at).toLocaleTimeString()}
                </TableCell>
                <TableCell align="right">
                  <Tooltip title="View Details">
                    <IconButton
                      size="small"
                      aria-label="View task details"
                      onClick={() => onSelectTask(task)}
                    >
                      <ViewIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>

                  {/* Pause Button - Phase 1.2 */}
                  {(task.status === 'in_progress' ||
                    task.status === 'pending') &&
                    onPauseTask && (
                      <Tooltip title="Pause">
                        <IconButton
                          size="small"
                          aria-label="Pause task"
                          onClick={() => onPauseTask(task.id)}
                        >
                          <PauseIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}

                  {/* Resume Button - Phase 1.2 */}
                  {task.status === 'paused' && onResumeTask && (
                    <Tooltip title="Resume">
                      <IconButton
                        size="small"
                        color="success"
                        aria-label="Resume task"
                        onClick={() => onResumeTask(task.id)}
                      >
                        <PlayIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}

                  {/* Cancel Button - Phase 1.2 */}
                  {['pending', 'in_progress', 'paused'].includes(task.status) &&
                    onCancelTask && (
                      <Tooltip title="Cancel">
                        <IconButton
                          size="small"
                          color="warning"
                          aria-label="Cancel task"
                          onClick={() => onCancelTask(task.id)}
                        >
                          <StopIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}

                  <Tooltip title="Edit">
                    <IconButton
                      size="small"
                      aria-label="Edit task"
                      onClick={() => onEditTask(task)}
                    >
                      <EditIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Delete">
                    <IconButton
                      size="small"
                      color="error"
                      aria-label="Delete task"
                      onClick={() => onDeleteTask(task.id)}
                    >
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Pagination Controls */}
      {total > limit && (
        <Box sx={{ display: 'flex', justifyContent: 'center' }}>
          <TablePagination
            component="div"
            count={total}
            page={page - 1}
            onPageChange={(e, newPage) => onPageChange(newPage + 1)}
            rowsPerPage={limit}
            onRowsPerPageChange={(e) =>
              onRowsPerPageChange(parseInt(e.target.value, 10))
            }
            rowsPerPageOptions={[10, 25, 50]}
          />
        </Box>
      )}
    </Box>
  );
};

TaskTable.propTypes = {
  tasks: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      name: PropTypes.string,
      task_type: PropTypes.string,
      status: PropTypes.string,
      created_at: PropTypes.string,
    })
  ),
  loading: PropTypes.bool,
  page: PropTypes.number,
  limit: PropTypes.number,
  total: PropTypes.number,
  selectedTasks: PropTypes.arrayOf(PropTypes.string),
  onSelectTask: PropTypes.func.isRequired,
  onSelectAll: PropTypes.func.isRequired,
  onSelectOne: PropTypes.func.isRequired,
  onPageChange: PropTypes.func.isRequired,
  onRowsPerPageChange: PropTypes.func.isRequired,
  onEditTask: PropTypes.func.isRequired,
  onDeleteTask: PropTypes.func.isRequired,
  onPauseTask: PropTypes.func,
  onResumeTask: PropTypes.func,
  onCancelTask: PropTypes.func,
};

export default TaskTable;
