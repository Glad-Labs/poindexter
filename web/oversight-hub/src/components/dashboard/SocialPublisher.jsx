import logger from '@/lib/logger';
import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  Alert,
  Stack,
  Button,
  TextField,
  FormControlLabel,
  Checkbox,
  Chip,
  Typography,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
} from '@mui/material';
import { Delete as DeleteIcon, Send as SendIcon } from '@mui/icons-material';
import {
  getPlatforms,
  createPost,
  getPosts,
  deletePost,
} from '../../services/socialService';

/**
 * SocialPublisher Component (Phase 2.3)
 *
 * Manages social media publishing:
 * - View connected platforms
 * - Create posts for multiple platforms
 * - Schedule posts
 * - View post history and analytics
 */
export const SocialPublisher = () => {
  const [activeTab, setActiveTab] = useState(0);
  const [platforms, setPlatforms] = useState({});
  const [posts, setPosts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  // Form state
  const [content, setContent] = useState('');
  const [selectedPlatforms, setSelectedPlatforms] = useState([]);
  const [scheduledTime, setScheduledTime] = useState('');
  const [tone, setTone] = useState('professional');
  const [includeHashtags, setIncludeHashtags] = useState(true);
  const [includeEmojis, setIncludeEmojis] = useState(true);

  useEffect(() => {
    loadPlatforms();
  }, []);

  useEffect(() => {
    if (activeTab === 1) {
      loadPosts();
    }
  }, [activeTab]);

  const loadPlatforms = async () => {
    try {
      const result = await getPlatforms();
      setPlatforms(result || {});
    } catch (err) {
      logger.error('Failed to load platforms:', err);
    }
  };

  const loadPosts = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getPosts({ limit: 50 });
      setPosts(result.posts || []);
    } catch (err) {
      setError(`Failed to load posts: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleCreatePost = async () => {
    if (!content.trim()) {
      setError('Please enter post content');
      return;
    }

    if (selectedPlatforms.length === 0) {
      setError('Please select at least one platform');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const postData = {
        content,
        platforms: selectedPlatforms,
        scheduled_time: scheduledTime || undefined,
        tone,
        include_hashtags: includeHashtags,
        include_emojis: includeEmojis,
      };

      const result = await createPost(postData);

      if (result.success || result.id) {
        setSuccess(true);
        setContent('');
        setSelectedPlatforms([]);
        setScheduledTime('');
        setTone('professional');
        setTimeout(() => setSuccess(false), 3000);

        // Refresh posts
        await loadPosts();
      } else {
        setError(result.message || 'Failed to create post');
      }
    } catch (err) {
      setError(`Failed to create post: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDeletePost = async (postId) => {
    if (!window.confirm('Are you sure you want to delete this post?')) {
      return;
    }

    try {
      await deletePost(postId);
      setPosts(posts.filter((p) => p.id !== postId));
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(`Failed to delete post: ${err.message}`);
    }
  };

  const togglePlatform = (platformName) => {
    if (selectedPlatforms.includes(platformName)) {
      setSelectedPlatforms(selectedPlatforms.filter((p) => p !== platformName));
    } else {
      setSelectedPlatforms([...selectedPlatforms, platformName]);
    }
  };

  return (
    <Box sx={{ width: '100%' }}>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 'bold', mb: 1 }}>
          Social Publisher
        </Typography>
        <Tabs value={activeTab} onChange={(e, v) => setActiveTab(v)}>
          <Tab label="Create Post" />
          <Tab label="Post History" />
        </Tabs>
      </Box>

      {/* Alerts */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {success && (
        <Alert severity="success" sx={{ mb: 2 }}>
          ✓ Post created successfully
        </Alert>
      )}

      {/* Tab 0: Create Post */}
      {activeTab === 0 && (
        <Card>
          <CardHeader title="Create New Post" />
          <CardContent>
            <Stack spacing={2}>
              {/* Platform Selection */}
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Select Platforms
                </Typography>
                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                  {Object.entries(platforms).map(([platformKey, platform]) => (
                    <Chip
                      key={platformKey}
                      label={platform.name}
                      onClick={() => togglePlatform(platformKey)}
                      color={
                        selectedPlatforms.includes(platformKey)
                          ? 'primary'
                          : 'default'
                      }
                      variant={
                        selectedPlatforms.includes(platformKey)
                          ? 'filled'
                          : 'outlined'
                      }
                      disabled={!platform.connected}
                      icon={!platform.connected ? undefined : undefined}
                    />
                  ))}
                </Box>
                <Typography variant="caption" color="textSecondary">
                  Only connected platforms can be selected
                </Typography>
              </Box>

              {/* Post Content */}
              <TextField
                fullWidth
                label="Post Content"
                placeholder="What would you like to post?"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                disabled={loading}
                multiline
                rows={4}
                inputProps={{ maxLength: 280 }}
                helperText={`${content.length}/280`}
              />

              {/* Tone Selection */}
              <TextField
                select
                value={tone}
                onChange={(e) => setTone(e.target.value)}
                fullWidth
                disabled={loading}
                SelectProps={{ native: true }}
              >
                <option value="professional">Professional</option>
                <option value="casual">Casual</option>
                <option value="funny">Funny</option>
                <option value="inspiring">Inspiring</option>
                <option value="urgent">Urgent</option>
              </TextField>

              {/* Post Options */}
              <Stack direction="row" spacing={2}>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={includeHashtags}
                      onChange={(e) => setIncludeHashtags(e.target.checked)}
                      disabled={loading}
                    />
                  }
                  label="Include hashtags"
                />
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={includeEmojis}
                      onChange={(e) => setIncludeEmojis(e.target.checked)}
                      disabled={loading}
                    />
                  }
                  label="Include emojis"
                />
              </Stack>

              {/* Schedule Time */}
              <TextField
                type="datetime-local"
                label="Schedule for later (Optional)"
                value={scheduledTime}
                onChange={(e) => setScheduledTime(e.target.value)}
                disabled={loading}
                InputLabelProps={{ shrink: true }}
              />

              {/* Submit Button */}
              <Button
                variant="contained"
                color="primary"
                startIcon={<SendIcon />}
                onClick={handleCreatePost}
                disabled={
                  loading || !content.trim() || selectedPlatforms.length === 0
                }
                fullWidth
              >
                {loading ? (
                  <CircularProgress size={24} />
                ) : (
                  'Create & Schedule Post'
                )}
              </Button>

              <Typography variant="caption" color="textSecondary">
                💡 Tip: You can schedule posts to be published at a specific
                time
              </Typography>
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Tab 1: Post History */}
      {activeTab === 1 && (
        <>
          <Box sx={{ mb: 2, display: 'flex', justifyContent: 'space-between' }}>
            <Typography variant="h6">
              Post History ({posts.length} posts)
            </Typography>
            <Button onClick={loadPosts} disabled={loading}>
              Refresh
            </Button>
          </Box>

          {loading && <CircularProgress />}

          {!loading && posts.length === 0 && (
            <Alert severity="info">No posts yet. Create your first post!</Alert>
          )}

          {!loading && posts.length > 0 && (
            <TableContainer component={Card}>
              <Table>
                <TableHead>
                  <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                    <TableCell>Content</TableCell>
                    <TableCell>Platforms</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Created</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {posts.map((post) => (
                    <TableRow key={post.id} hover>
                      <TableCell sx={{ maxWidth: 200, whiteSpace: 'nowrap' }}>
                        {post.content.substring(0, 50)}...
                      </TableCell>
                      <TableCell>
                        {post.platforms?.map((p) => (
                          <Chip
                            key={p}
                            label={p}
                            size="small"
                            sx={{ mr: 0.5 }}
                          />
                        ))}
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={post.status || 'unknown'}
                          size="small"
                          color={
                            post.status === 'published' ? 'success' : 'default'
                          }
                        />
                      </TableCell>
                      <TableCell>
                        {new Date(post.created_at).toLocaleDateString()}
                      </TableCell>
                      <TableCell align="right">
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => handleDeletePost(post.id)}
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </>
      )}
    </Box>
  );
};

export default SocialPublisher;
