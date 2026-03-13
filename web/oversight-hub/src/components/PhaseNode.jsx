import React from 'react';
import { Handle, Position } from 'reactflow';
import { Box, Paper, Typography, Chip } from '@mui/material';
import { Clock, RotateCcw } from 'lucide-react';

const PhaseNode = ({ data, _isConnecting, isSelected }) => {
  return (
    <Paper
      sx={{
        p: 1.5,
        minWidth: 180,
        textAlign: 'center',
        backgroundColor: isSelected ? '#e3f2fd' : '#fff',
        border: isSelected ? '2px solid #2196f3' : '1px solid #bbb',
        borderRadius: 1,
        boxShadow: isSelected
          ? '0 0 8px rgba(33, 150, 243, 0.5)'
          : '0 2px 4px rgba(0,0,0,0.1)',
        transition: 'all 0.2s',
        cursor: 'pointer',
      }}
    >
      <Handle type="target" position={Position.Left} />

      <Typography
        variant="subtitle2"
        sx={{
          fontWeight: 600,
          textTransform: 'capitalize',
          color: '#1976d2',
          mb: 0.5,
        }}
      >
        {data.phase?.name || data.label}
      </Typography>

      {data.phase?.agent && (
        <Chip
          label={data.phase.agent}
          size="small"
          variant="outlined"
          sx={{ mb: 0.5 }}
        />
      )}

      <Box
        sx={{
          display: 'flex',
          gap: 0.5,
          justifyContent: 'center',
          fontSize: '0.75rem',
          color: '#666',
        }}
      >
        {data.phase?.timeout_seconds && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
            <Clock size={12} />
            <span>{data.phase.timeout_seconds}s</span>
          </Box>
        )}
        {data.phase?.max_retries > 0 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
            <RotateCcw size={12} />
            <span>{data.phase.max_retries}x</span>
          </Box>
        )}
      </Box>

      <Handle type="source" position={Position.Right} />
    </Paper>
  );
};

export default PhaseNode;
