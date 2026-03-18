/**
 * TaskImageManager - Image selection and generation
 *
 * Features:
 * - Image source selection (Pexels, SDXL)
 * - Manual image URL input
 * - Generate image button
 * - Display selected image
 */

import React from 'react';
import PropTypes from 'prop-types';
import { Box, Button, TextField } from '@mui/material';

const TaskImageManager = ({
  task,
  imageSource,
  selectedImageUrl,
  imageGenerating,
  onImageSourceChange,
  onImageUrlChange,
  onGenerateImage,
}) => {
  if (!task || !['awaiting_approval', 'rejected'].includes(task.status)) {
    return null;
  }

  return (
    <Box
      sx={{
        background: 'linear-gradient(135deg, #1a2a3a 0%, #1a2a1a 100%)',
        padding: 2,
        borderRadius: 1,
        border: '1px solid #00d9ff',
      }}
    >
      <h3 style={{ marginTop: 0, color: '#00d9ff' }}>🎨 Image Management</h3>

      <Box sx={{ mb: 2 }}>
        <label
          style={{
            display: 'block',
            marginBottom: '8px',
            fontWeight: 'bold',
            color: '#e0e0e0',
          }}
        >
          Image Source:
        </label>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant={imageSource === 'pexels' ? 'contained' : 'outlined'}
            onClick={() => onImageSourceChange('pexels')}
            size="small"
            sx={{
              color: imageSource === 'pexels' ? '#fff' : '#00d9ff',
              backgroundColor:
                imageSource === 'pexels' ? '#00d9ff' : 'transparent',
              borderColor: '#00d9ff',
              '&:hover': {
                backgroundColor:
                  imageSource === 'pexels'
                    ? '#00c2d4'
                    : 'rgba(0, 217, 255, 0.1)',
              },
            }}
          >
            📷 Pexels
          </Button>
          <Button
            variant={imageSource === 'sdxl' ? 'contained' : 'outlined'}
            onClick={() => onImageSourceChange('sdxl')}
            size="small"
            sx={{
              color: imageSource === 'sdxl' ? '#fff' : '#00d9ff',
              backgroundColor:
                imageSource === 'sdxl' ? '#00d9ff' : 'transparent',
              borderColor: '#00d9ff',
              '&:hover': {
                backgroundColor:
                  imageSource === 'sdxl' ? '#00c2d4' : 'rgba(0, 217, 255, 0.1)',
              },
            }}
          >
            🤖 SDXL
          </Button>
        </Box>
      </Box>

      {/* Image URL Input */}
      <TextField
        fullWidth
        size="small"
        label="Image URL (or generate below)"
        value={selectedImageUrl}
        onChange={(e) => onImageUrlChange(e.target.value)}
        placeholder="https://..."
        sx={{
          mb: 2,
          '& .MuiOutlinedInput-root': {
            backgroundColor: '#0f0f0f',
            borderColor: '#333',
            color: '#e0e0e0',
            '&:hover fieldset': {
              borderColor: '#00d9ff',
            },
          },
          '& .MuiInputBase-input::placeholder': {
            color: '#666',
            opacity: 1,
          },
          '& .MuiInputLabel-root': {
            color: '#999',
          },
        }}
      />

      {/* Generate Image Button */}
      <Box sx={{ mb: 2, display: 'flex', gap: 1 }}>
        <Button
          fullWidth
          variant="contained"
          sx={{
            backgroundColor: '#8b5cf6',
            '&:hover': { backgroundColor: '#7c3aed' },
          }}
          onClick={() => onGenerateImage(imageSource)}
          disabled={imageGenerating}
        >
          {imageGenerating ? '⟳ Generating...' : '✨ Generate Image'}
        </Button>
      </Box>

      {/* Preview of selected image */}
      {selectedImageUrl && (
        <Box>
          <p style={{ margin: '12px 0 8px 0', color: '#e0e0e0' }}>
            📸 Preview:
          </p>
          <Box
            component="img"
            src={selectedImageUrl}
            alt={
              task?.topic
                ? `Preview image for task: ${task.topic}`
                : 'Selected task image preview'
            }
            sx={{
              maxWidth: '100%',
              maxHeight: '250px',
              borderRadius: 1,
              border: '1px solid #00d9ff',
            }}
          />
        </Box>
      )}
    </Box>
  );
};

TaskImageManager.propTypes = {
  task: PropTypes.shape({
    status: PropTypes.string.isRequired,
    topic: PropTypes.string,
  }),
  imageSource: PropTypes.string.isRequired,
  selectedImageUrl: PropTypes.string.isRequired,
  imageGenerating: PropTypes.bool.isRequired,
  onImageSourceChange: PropTypes.func.isRequired,
  onImageUrlChange: PropTypes.func.isRequired,
  onGenerateImage: PropTypes.func.isRequired,
};

export default TaskImageManager;
