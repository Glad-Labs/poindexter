import logger from '@/lib/logger';
/**
 * TaskMetadataDisplay - Display task metadata in grid format
 *
 * Features:
 * - Category
 * - Writing style
 * - Target audience
 * - Word count
 * - SEO metadata (keywords, title, description)
 * - Quality metrics
 */

import React from 'react';
import PropTypes from 'prop-types';
import { Box, Grid, Paper } from '@mui/material';

/**
 * Helper to get quality score badge color and label
 */
const getQualityBadge = (score) => {
  if (typeof score !== 'number') return { label: 'N/A', color: '#666' };
  // Handle both 0-100 scale and legacy 0-10 scale
  // Only scores ≤ 5 are treated as legacy 0-5 scale (quality_service returns 0-100)
  const normalizedScore = score <= 5 ? score * 20 : score;
  if (normalizedScore >= 90) return { label: 'Excellent', color: '#4ade80' };
  if (normalizedScore >= 75) return { label: 'Good', color: '#22c55e' };
  if (normalizedScore >= 50) return { label: 'Fair', color: '#eab308' };
  return { label: 'Poor', color: '#ef4444' };
};

const TaskMetadataDisplay = ({ task }) => {
  if (!task) return null;

  // Extract metadata from multiple possible sources
  const taskMeta = task.task_metadata || {};
  const result = task.result || {};
  const extractedMetadata =
    task.extracted_metadata || taskMeta.extracted_metadata || {};

  // Parse metadata if it's a JSON string
  let parsedTaskMeta = taskMeta;
  if (typeof taskMeta === 'string') {
    try {
      parsedTaskMeta = JSON.parse(taskMeta);
    } catch (error) {
      logger.error('Failed to parse task metadata:', error);
      parsedTaskMeta = {};
    }
  }

  // Parse result if it's a JSON string
  let parsedResult = result;
  if (typeof result === 'string') {
    try {
      parsedResult = JSON.parse(result);
    } catch (error) {
      logger.error('Failed to parse task result:', error);
      parsedResult = {};
    }
  }

  // Extract SEO data from multiple sources
  const seoData =
    task.seo_keywords ||
    parsedTaskMeta.seo_keywords ||
    parsedResult.seo_keywords ||
    {};

  // Get quality score from task or metadata
  const qualityScore =
    task.quality_score ||
    parsedTaskMeta.quality_score ||
    parsedResult.quality_score;
  const qualityBadge = getQualityBadge(qualityScore);

  // Get word count - try content length if not specified
  let wordCount =
    extractedMetadata.word_count ||
    parsedTaskMeta.target_length ||
    parsedTaskMeta.word_count ||
    task.target_length;

  if (!wordCount && (parsedTaskMeta.content || parsedResult.content)) {
    const content = parsedTaskMeta.content || parsedResult.content;
    wordCount = content.split(/\\s+/).length;
  }

  // Extract timing information
  const createdAt = task.created_at
    ? new Date(task.created_at).toLocaleString()
    : 'N/A';
  const startedAt = task.started_at
    ? new Date(task.started_at).toLocaleString()
    : 'N/A';
  const completedAt = task.completed_at
    ? new Date(task.completed_at).toLocaleString()
    : 'N/A';

  // Calculate execution time if available
  let executionTime = 'N/A';
  if (task.started_at && task.completed_at) {
    const start = new Date(task.started_at);
    const end = new Date(task.completed_at);
    const diffMs = end - start;
    const diffMins = Math.round(diffMs / 60000);
    executionTime = `${diffMins} min${diffMins !== 1 ? 's' : ''}`;
  }

  logger.debug('TaskMetadataDisplay task data', {
    id: task.id,
    model_used: task.model_used,
    selected_model: task.selected_model,
    model: task.model,
    target_length: task.target_length,
    parsedResult,
    parsedTaskMeta,
    extractedMetadata,
    task,
  });

  const metadataItems = [
    {
      label: 'Category',
      value:
        task.category ||
        extractedMetadata.category ||
        parsedTaskMeta.category ||
        'Not specified',
    },
    {
      label: 'Style',
      value:
        task.style ||
        extractedMetadata.writing_style ||
        parsedTaskMeta.style ||
        'Not specified',
    },
    {
      label: 'Target Audience',
      value:
        task.target_audience ||
        extractedMetadata.target_audience ||
        parsedTaskMeta.target_audience ||
        'Not specified',
    },
    {
      label: 'Word Count',
      value: (() => {
        const target = task.target_length || parsedTaskMeta.target_length;
        const actual =
          extractedMetadata.word_count ||
          parsedTaskMeta.word_count ||
          (parsedResult.content
            ? parsedResult.content.split(/\s+/).length
            : null);

        if (target && actual) {
          const percentage = ((actual / target) * 100).toFixed(0);
          const color =
            percentage >= 90 && percentage <= 110 ? '#4ade80' : '#eab308';
          return (
            <span>
              {actual} / {target} words{' '}
              <span style={{ color }}>({percentage}%)</span>
            </span>
          );
        } else if (actual) {
          return `${actual} words`;
        } else if (target) {
          return `Target: ${target} words`;
        }
        return 'Not specified';
      })(),
    },
    {
      label: 'Quality Score',
      value: qualityScore
        ? `${qualityScore.toFixed(2)}/100 (${qualityBadge.label})`
        : 'Not rated',
      color: qualityBadge.color,
    },
    {
      label: 'Status',
      value: task.status || 'Unknown',
      color:
        task.status === 'completed'
          ? '#4ade80'
          : task.status === 'failed'
            ? '#ef4444'
            : task.status === 'published'
              ? '#8b5cf6'
              : '#eab308',
    },
    {
      label: 'Created',
      value: createdAt,
    },
    {
      label: 'Started',
      value: startedAt,
    },
    {
      label: 'Completed',
      value: completedAt,
    },
    {
      label: 'Execution Time',
      value: executionTime,
    },
    {
      label: 'Task Type',
      value: task.task_type || 'Standard',
    },
    {
      label: 'Model Used',
      value:
        task.model_used ||
        task.selected_model ||
        task.model ||
        parsedTaskMeta.model_used ||
        parsedTaskMeta.model ||
        parsedTaskMeta.selected_model ||
        parsedResult.model_used ||
        parsedResult.model ||
        parsedResult.selected_model ||
        'Not specified',
      color: '#a78bfa',
    },
    {
      label: 'Published',
      value: task.is_published ? 'Yes' : 'No',
      color: task.is_published ? '#4ade80' : '#ef4444',
    },
    {
      label: 'Featured Image Source',
      value: parsedTaskMeta.featured_image_source || 'Not set',
    },
    {
      label: 'Last Modified',
      value: task.updated_at
        ? new Date(task.updated_at).toLocaleString()
        : 'Not available',
    },
    {
      label: 'Approval Status',
      value: task.approval_status || 'Not specified',
      color:
        task.approval_status === 'approved'
          ? '#4ade80'
          : task.approval_status === 'rejected'
            ? '#ef4444'
            : '#eab308',
    },
  ];

  return (
    <Box>
      <h3
        style={{
          margin: '0 0 12px 0',
          color: '#00d9ff',
          fontSize: '1rem',
        }}
      >
        📊 Metadata & Metrics
      </h3>

      <Grid container spacing={1.5}>
        {metadataItems.map((item, idx) => (
          <Grid
            key={idx}
            sx={{
              width: '100%',
              '@media (min-width: 600px)': {
                width: 'calc(50% - 12px)',
              },
            }}
          >
            <Paper
              sx={{
                padding: '12px',
                backgroundColor: 'rgba(15, 15, 15, 0.5)',
                borderRadius: '4px',
                border: '1px solid rgba(0, 217, 255, 0.2)',
              }}
            >
              <p
                style={{
                  margin: '0 0 6px 0',
                  color: '#999',
                  fontSize: '0.75rem',
                  fontWeight: 'bold',
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                }}
              >
                {item.label}
              </p>
              <p
                style={{
                  margin: 0,
                  color: item.color || '#e0e0e0',
                  fontSize: '0.95rem',
                  fontWeight: item.color ? 'bold' : 'normal',
                }}
              >
                {item.value}
              </p>
            </Paper>
          </Grid>
        ))}
      </Grid>

      {/* SEO Metadata Section */}
      {(seoData.keywords || seoData.title || seoData.description) && (
        <Box sx={{ marginTop: 2 }}>
          <h4
            style={{
              margin: '0 0 12px 0',
              color: '#a78bfa',
              fontSize: '0.95rem',
            }}
          >
            🔍 SEO Metadata
          </h4>

          {seoData.title && (
            <Box
              sx={{
                marginBottom: '12px',
                padding: '10px',
                backgroundColor: 'rgba(167, 139, 250, 0.1)',
                borderRadius: '4px',
                borderLeft: '3px solid #a78bfa',
              }}
            >
              <p
                style={{
                  margin: '0 0 4px 0',
                  color: '#a78bfa',
                  fontSize: '0.75rem',
                  fontWeight: 'bold',
                }}
              >
                SEO Title
              </p>
              <p style={{ margin: 0, color: '#e0e0e0', fontSize: '0.9rem' }}>
                {seoData.title}
              </p>
            </Box>
          )}

          {seoData.description && (
            <Box
              sx={{
                marginBottom: '12px',
                padding: '10px',
                backgroundColor: 'rgba(167, 139, 250, 0.1)',
                borderRadius: '4px',
                borderLeft: '3px solid #a78bfa',
              }}
            >
              <p
                style={{
                  margin: '0 0 4px 0',
                  color: '#a78bfa',
                  fontSize: '0.75rem',
                  fontWeight: 'bold',
                }}
              >
                SEO Description
              </p>
              <p style={{ margin: 0, color: '#e0e0e0', fontSize: '0.9rem' }}>
                {seoData.description}
              </p>
            </Box>
          )}

          {seoData.keywords && (
            <Box
              sx={{
                padding: '10px',
                backgroundColor: 'rgba(167, 139, 250, 0.1)',
                borderRadius: '4px',
                borderLeft: '3px solid #a78bfa',
              }}
            >
              <p
                style={{
                  margin: '0 0 8px 0',
                  color: '#a78bfa',
                  fontSize: '0.75rem',
                  fontWeight: 'bold',
                }}
              >
                Keywords
              </p>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {Array.isArray(seoData.keywords) ? (
                  seoData.keywords.map((kw, idx) => (
                    <Box
                      key={idx}
                      sx={{
                        display: 'inline-block',
                        padding: '4px 8px',
                        backgroundColor: 'rgba(167, 139, 250, 0.2)',
                        borderRadius: '3px',
                        border: '1px solid rgba(167, 139, 250, 0.4)',
                        color: '#d8b4fe',
                        fontSize: '0.85rem',
                      }}
                    >
                      {kw}
                    </Box>
                  ))
                ) : (
                  <p
                    style={{
                      margin: 0,
                      color: '#d8b4fe',
                      fontSize: '0.85rem',
                    }}
                  >
                    {seoData.keywords}
                  </p>
                )}
              </Box>
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
};

TaskMetadataDisplay.propTypes = {
  task: PropTypes.shape({
    extracted_metadata: PropTypes.object,
    seo_keywords: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
    quality_score: PropTypes.number,
  }),
};

export default TaskMetadataDisplay;
