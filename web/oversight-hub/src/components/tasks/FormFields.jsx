/**
 * FormFields.jsx
 *
 * Renders dynamic form fields based on task type definition
 * Handles:
 * - Text, number, select, textarea, checkbox, range inputs
 * - Validation and error display
 * - Field descriptions and defaults
 *
 * Props:
 * - fields: Array of field definitions from task type
 * - values: Current form values
 * - onChange: Callback when field value changes
 * - errors: Object with field errors
 */

import React from 'react';
import {
  TextField,
  Select,
  MenuItem,
  Checkbox,
  FormControlLabel,
  FormControl,
  InputLabel,
  Slider,
  Box,
  FormHelperText,
  Typography,
} from '@mui/material';

const FormFields = ({ fields = [], values = {}, onChange, errors = {} }) => {
  const handleChange = (fieldName, value) => {
    onChange({ [fieldName]: value });
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {fields.map((field) => {
        const value = values[field.name] ?? field.defaultValue ?? '';
        const error = errors[field.name];

        if (field.type === 'text' || field.type === 'number') {
          return (
            <TextField
              key={field.name}
              label={field.label}
              type={field.type}
              value={value}
              onChange={(e) => handleChange(field.name, e.target.value)}
              fullWidth
              required={field.required}
              error={!!error}
              helperText={error || field.description}
              inputProps={{
                min: field.min,
                max: field.max,
                step: field.step,
              }}
              variant="outlined"
              size="small"
            />
          );
        }

        if (field.type === 'textarea') {
          return (
            <TextField
              key={field.name}
              label={field.label}
              value={value}
              onChange={(e) => handleChange(field.name, e.target.value)}
              fullWidth
              multiline
              rows={4}
              required={field.required}
              error={!!error}
              helperText={error || field.description}
              variant="outlined"
              size="small"
            />
          );
        }

        if (field.type === 'select') {
          const labelId = `${field.name}-label`;
          return (
            <FormControl
              fullWidth
              key={field.name}
              error={!!error}
              size="small"
            >
              <InputLabel id={labelId}>{field.label}</InputLabel>
              <Select
                labelId={labelId}
                value={value}
                label={field.label}
                onChange={(e) => handleChange(field.name, e.target.value)}
              >
                {field.options?.map((option) => (
                  <MenuItem key={option} value={option}>
                    {option.charAt(0).toUpperCase() +
                      option.slice(1).replace('-', ' ')}
                  </MenuItem>
                ))}
              </Select>
              {field.description && (
                <FormHelperText>{field.description}</FormHelperText>
              )}
            </FormControl>
          );
        }

        if (field.type === 'checkbox') {
          return (
            <FormControlLabel
              key={field.name}
              control={
                <Checkbox
                  checked={!!value}
                  onChange={(e) => handleChange(field.name, e.target.checked)}
                />
              }
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {field.label}
                  </Typography>
                  {field.description && (
                    <Typography
                      variant="caption"
                      sx={{ display: 'block', opacity: 0.7 }}
                    >
                      {field.description}
                    </Typography>
                  )}
                </Box>
              }
            />
          );
        }

        if (field.type === 'range') {
          return (
            <Box key={field.name}>
              <Typography variant="body2" sx={{ fontWeight: 500, mb: 1 }}>
                {field.label}: <strong>{value}%</strong>
              </Typography>
              <Slider
                value={value}
                onChange={(e, newValue) => handleChange(field.name, newValue)}
                min={field.min}
                max={field.max}
                step={field.step}
                marks
                valueLabelDisplay="auto"
              />
              {field.description && (
                <Typography
                  variant="caption"
                  sx={{ display: 'block', mt: 1, opacity: 0.7 }}
                >
                  {field.description}
                </Typography>
              )}
            </Box>
          );
        }

        return null;
      })}
    </Box>
  );
};

export default FormFields;
