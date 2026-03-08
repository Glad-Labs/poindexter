import React, { useState, useEffect } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  CardHeader,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  CircularProgress,
  Alert,
  Typography,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  IconButton,
  Chip,
  Divider,
  Paper,
  LinearProgress,
} from '@mui/material';
import {
  Delete as DeleteIcon,
  Edit as EditIcon,
  CheckCircle as CheckCircleIcon,
  CloudUpload as CloudUploadIcon,
  Info as InfoIcon,
} from '@mui/icons-material';
import {
  uploadWritingSample,
  getUserWritingSamples,
  deleteWritingSample,
  setActiveWritingSample,
  updateWritingSample,
  getActiveWritingSample,
} from '../services/writingStyleService';

/**
 * WritingStyleManager
 *
 * Component for managing user writing samples used for RAG-based style matching.
 * Allows uploading, editing, deleting, and activating writing samples.
 */
export const WritingStyleManager = () => {
  const [samples, setSamples] = useState([]);
  const [activeSample, setActiveSample] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [openDialog, setOpenDialog] = useState(false);
  const [dialogMode, setDialogMode] = useState('create'); // 'create' or 'edit'
  const [selectedFile, setSelectedFile] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState({
    title: '',
    description: '',
    content: '',
  });

  // Load writing samples on mount
  useEffect(() => {
    loadWritingSamples();
  }, []);

  const loadWritingSamples = async () => {
    try {
      setLoading(true);
      setError(null);
      const [samplesRes, activeRes] = await Promise.all([
        getUserWritingSamples(),
        getActiveWritingSample(),
      ]);
      setSamples(samplesRes.samples || []);
      setActiveSample(activeRes.sample || null);
    } catch (err) {
      setError(`Failed to load writing samples: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleOpenDialog = (mode, sample = null) => {
    setDialogMode(mode);
    if (mode === 'edit' && sample) {
      setEditingId(sample.id);
      setFormData({
        title: sample.title,
        description: sample.description || '',
        content: sample.preview || '',
      });
    } else {
      setEditingId(null);
      setFormData({ title: '', description: '', content: '' });
    }
    setSelectedFile(null);
    setOpenDialog(true);
  };

  const handleCloseDialog = () => {
    setOpenDialog(false);
    setFormData({ title: '', description: '', content: '' });
    setSelectedFile(null);
    setError(null);
  };

  const handleFormChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: value,
    }));
  };

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) {
      // Validate file size (max 1MB)
      if (file.size > 1024 * 1024) {
        setError('File size must be less than 1MB');
        return;
      }
      // Validate file type
      if (
        !['text/plain', 'text/markdown', 'application/pdf'].includes(file.type)
      ) {
        setError('File must be TXT, MD, or PDF');
        return;
      }
      setSelectedFile(file);
      setError(null);
    }
  };

  const handleSubmit = async () => {
    try {
      setError(null);
      setUploading(true);

      // Validate required fields
      if (!formData.title.trim()) {
        setError('Title is required');
        setUploading(false);
        return;
      }

      if (dialogMode === 'create') {
        // For create mode, need either file or content
        if (!selectedFile && !formData.content.trim()) {
          setError('Please upload a file or paste content');
          setUploading(false);
          return;
        }

        const content = selectedFile || formData.content;
        await uploadWritingSample(
          formData.title,
          formData.description,
          content,
          samples.length === 0 // Auto-activate if first sample
        );
        setSuccess('Writing sample uploaded successfully');
      } else if (dialogMode === 'edit') {
        // For edit mode, only update title and description if no new content
        await updateWritingSample(editingId, {
          title: formData.title,
          description: formData.description,
          ...(selectedFile && { content: selectedFile }),
        });
        setSuccess('Writing sample updated successfully');
      }

      handleCloseDialog();
      await loadWritingSamples();
    } catch (err) {
      setError(`Failed to save writing sample: ${err.message}`);
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (sampleId) => {
    if (
      !window.confirm('Are you sure you want to delete this writing sample?')
    ) {
      return;
    }

    try {
      setError(null);
      await deleteWritingSample(sampleId);
      setSuccess('Writing sample deleted successfully');
      await loadWritingSamples();
    } catch (err) {
      setError(`Failed to delete writing sample: ${err.message}`);
    }
  };

  const handleSetActive = async (sampleId) => {
    try {
      setError(null);
      await setActiveWritingSample(sampleId);
      setSuccess('Active writing sample updated');
      await loadWritingSamples();
    } catch (err) {
      setError(`Failed to set active sample: ${err.message}`);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Writing Style Manager"
        subheader="Upload and manage writing samples for RAG-based style matching"
        action={
          <Button
            variant="contained"
            startIcon={<CloudUploadIcon />}
            onClick={() => handleOpenDialog('create')}
            size="small"
          >
            Upload Sample
          </Button>
        }
      />
      <Divider />
      <CardContent>
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {success && (
          <Alert
            severity="success"
            onClose={() => setSuccess(null)}
            sx={{ mb: 2 }}
          >
            {success}
          </Alert>
        )}

        {/* Information Banner */}
        <Paper
          sx={{
            p: 2,
            mb: 3,
            bgcolor: 'info.light',
            border: '1px solid',
            borderColor: 'info.main',
          }}
        >
          <Box sx={{ display: 'flex', gap: 1 }}>
            <InfoIcon sx={{ color: 'info.main', flexShrink: 0 }} />
            <Typography variant="body2" color="info.dark">
              <strong>How it works:</strong> Upload writing samples that
              represent your desired writing style. When creating content, the
              system will automatically match your style using RAG retrieval.
              The active sample is used for all new content generation tasks.
            </Typography>
          </Box>
        </Paper>

        {samples.length === 0 ? (
          <Alert severity="info">
            No writing samples uploaded yet. Upload your first sample to get
            started.
          </Alert>
        ) : (
          <List>
            {samples.map((sample) => (
              <React.Fragment key={sample.id}>
                <ListItem
                  sx={{
                    bgcolor:
                      activeSample?.id === sample.id
                        ? 'action.selected'
                        : 'transparent',
                    borderRadius: 1,
                    mb: 1,
                    border:
                      activeSample?.id === sample.id
                        ? '2px solid'
                        : '1px solid',
                    borderColor:
                      activeSample?.id === sample.id
                        ? 'primary.main'
                        : 'divider',
                  }}
                >
                  <ListItemText
                    primaryTypographyProps={{ component: 'div' }}
                    secondaryTypographyProps={{ component: 'div' }}
                    primary={
                      <Box
                        sx={{ display: 'flex', alignItems: 'center', gap: 1 }}
                      >
                        <Typography variant="subtitle1">
                          {sample.title}
                        </Typography>
                        {activeSample?.id === sample.id && (
                          <Chip
                            icon={<CheckCircleIcon />}
                            label="Active"
                            size="small"
                            color="primary"
                            variant="outlined"
                          />
                        )}
                      </Box>
                    }
                    secondary={
                      <Box>
                        <Typography variant="body2" color="text.secondary">
                          {sample.description}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ mt: 0.5, display: 'block' }}
                        >
                          Words: {sample.word_count || 0} | Updated:{' '}
                          {new Date(sample.updated_at).toLocaleDateString()}
                        </Typography>
                      </Box>
                    }
                  />
                  <ListItemSecondaryAction>
                    <Box sx={{ display: 'flex', gap: 1 }}>
                      {activeSample?.id !== sample.id && (
                        <Button
                          size="small"
                          onClick={() => handleSetActive(sample.id)}
                          disabled={uploading}
                        >
                          Set Active
                        </Button>
                      )}
                      <IconButton
                        edge="end"
                        onClick={() => handleOpenDialog('edit', sample)}
                        disabled={uploading}
                        size="small"
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        edge="end"
                        onClick={() => handleDelete(sample.id)}
                        disabled={uploading}
                        size="small"
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Box>
                  </ListItemSecondaryAction>
                </ListItem>
              </React.Fragment>
            ))}
          </List>
        )}
      </CardContent>

      {/* Upload/Edit Dialog */}
      <Dialog
        open={openDialog}
        onClose={handleCloseDialog}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {dialogMode === 'create'
            ? 'Upload Writing Sample'
            : 'Edit Writing Sample'}
        </DialogTitle>
        <DialogContent sx={{ pt: 2 }}>
          <TextField
            autoFocus
            margin="dense"
            name="title"
            label="Sample Title"
            fullWidth
            variant="outlined"
            value={formData.title}
            onChange={handleFormChange}
            placeholder="e.g., Blog Post Style, Email Copy"
            disabled={uploading}
            sx={{ mb: 2 }}
          />

          <TextField
            margin="dense"
            name="description"
            label="Description (optional)"
            fullWidth
            variant="outlined"
            multiline
            rows={2}
            value={formData.description}
            onChange={handleFormChange}
            placeholder="Describe the style, tone, and use case of this sample"
            disabled={uploading}
            sx={{ mb: 2 }}
          />

          {dialogMode === 'create' && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                Upload File or Paste Content
              </Typography>
              <Button
                variant="outlined"
                component="label"
                fullWidth
                disabled={uploading}
                sx={{ mb: 2 }}
              >
                Choose File (TXT, MD, PDF)
                <input
                  type="file"
                  hidden
                  accept=".txt,.md,.pdf"
                  onChange={handleFileSelect}
                />
              </Button>

              {selectedFile && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)}{' '}
                  KB)
                </Alert>
              )}

              {!selectedFile && (
                <>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mb: 1 }}
                  >
                    or
                  </Typography>
                  <TextField
                    name="content"
                    label="Paste Content"
                    fullWidth
                    multiline
                    rows={6}
                    variant="outlined"
                    value={formData.content}
                    onChange={handleFormChange}
                    placeholder="Paste your writing sample here..."
                    disabled={uploading}
                  />
                </>
              )}
            </Box>
          )}

          {uploading && <LinearProgress sx={{ mb: 2 }} />}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseDialog} disabled={uploading}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            variant="contained"
            disabled={uploading}
          >
            {uploading
              ? 'Uploading...'
              : dialogMode === 'create'
                ? 'Upload'
                : 'Update'}
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
};

export default WritingStyleManager;
