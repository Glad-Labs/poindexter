import React, { useState, useEffect } from 'react';
import {
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  CircularProgress,
  FormHelperText,
  Box,
  Chip,
} from '@mui/material';
import {
  getActiveWritingSample,
  getUserWritingSamples,
} from '../services/writingStyleService';

/**
 * WritingStyleSelector
 *
 * A form control for selecting which writing sample to use for content generation.
 * Can be used in task creation modals and content generation forms.
 *
 * Props:
 * - value: Currently selected sample ID
 * - onChange: Callback when selection changes
 * - required: Whether the field is required
 * - variant: MUI TextField variant
 * - disabled: Whether the field is disabled
 */
export const WritingStyleSelector = ({
  value,
  onChange,
  required = false,
  variant = 'outlined',
  disabled = false,
  includeNone = true,
}) => {
  const [samples, setSamples] = useState([]);
  const [activeSample, setActiveSample] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const loadSamples = async () => {
      try {
        setLoading(true);
        setError(null);
        const [samplesRes, activeRes] = await Promise.all([
          getUserWritingSamples(),
          getActiveWritingSample(),
        ]);
        setSamples(samplesRes.samples || []);
        setActiveSample(activeRes.sample || null);

        // Auto-select active sample if no value provided
        if (!value && activeRes.sample) {
          onChange(activeRes.sample.id);
        }
      } catch (err) {
        setError(`Failed to load writing samples: ${err.message}`);
      } finally {
        setLoading(false);
      }
    };

    loadSamples();
  }, [onChange, value]);

  if (loading) {
    return (
      <FormControl fullWidth disabled>
        <InputLabel id="writing-style-label">Writing Style</InputLabel>
        <Select
          labelId="writing-style-label"
          label="Writing Style"
          value=""
          disabled
        >
          <MenuItem value="">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <CircularProgress size={20} />
              <span>Loading...</span>
            </Box>
          </MenuItem>
        </Select>
      </FormControl>
    );
  }

  if (error) {
    return (
      <FormControl fullWidth error disabled>
        <InputLabel id="writing-style-label">Writing Style</InputLabel>
        <Select labelId="writing-style-label" label="Writing Style" value="">
          <MenuItem value="">Error loading samples</MenuItem>
        </Select>
        <FormHelperText>{error}</FormHelperText>
      </FormControl>
    );
  }

  if (samples.length === 0) {
    return (
      <FormControl fullWidth disabled>
        <InputLabel id="writing-style-label">Writing Style</InputLabel>
        <Select labelId="writing-style-label" label="Writing Style" value="">
          <MenuItem value="">No writing samples available</MenuItem>
        </Select>
        <FormHelperText>
          Upload a writing sample in Settings to use style matching
        </FormHelperText>
      </FormControl>
    );
  }

  return (
    <FormControl fullWidth required={required} variant={variant}>
      <InputLabel id="writing-style-label">Writing Style</InputLabel>
      <Select
        labelId="writing-style-label"
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        label="Writing Style"
      >
        {includeNone && (
          <MenuItem value="">
            <em>None - Use default style</em>
          </MenuItem>
        )}
        {samples.map((sample) => (
          <MenuItem key={sample.id} value={sample.id}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                width: '100%',
              }}
            >
              <span>{sample.title}</span>
              {activeSample?.id === sample.id && (
                <Chip
                  label="Active"
                  size="small"
                  variant="outlined"
                  sx={{ ml: 'auto' }}
                />
              )}
            </Box>
          </MenuItem>
        ))}
      </Select>
      <FormHelperText>
        Select a writing style to match when generating content
      </FormHelperText>
    </FormControl>
  );
};

export default WritingStyleSelector;
