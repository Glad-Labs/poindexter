# WebSocket Progress Tracking - Frontend Integration Guide

## Quick Start

### 1. Connect to WebSocket and Listen for Progress

```javascript
// In your React/Vue component that triggers image generation

const connectToProgress = (taskId) => {
  const ws = new WebSocket(`ws://localhost:8000/ws/image-generation/${taskId}`);

  ws.onopen = () => {
    console.log(`Connected to progress stream for task ${taskId}`);
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.type === 'progress') {
      // Update UI with progress
      updateProgressBar(data.percentage);
      updateProgressText(`${data.current_step}/${data.total_steps}`);
      updateStageLabel(data.current_stage);

      if (data.elapsed_time) {
        updateElapsedTime(data.elapsed_time);
      }

      if (data.estimated_remaining) {
        updateRemainingTime(data.estimated_remaining);
      }
    }
  };

  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
  };

  ws.onclose = () => {
    console.log('Progress stream closed');
  };

  return ws;
};
```

### 2. Call Image Generation API with task_id

```javascript
const generateImageWithProgress = async (prompt) => {
  // Generate a unique task ID
  const taskId = `task-${Date.now()}`;

  // Connect to progress stream
  const ws = connectToProgress(taskId);

  try {
    // Call the generation endpoint
    const response = await fetch('http://localhost:8000/api/media/generate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authToken}`,
      },
      body: JSON.stringify({
        prompt: prompt,
        use_generation: true,
        use_refinement: true,
        high_quality: true,
        task_id: taskId, // <- Pass task_id for progress tracking
      }),
    });

    const result = await response.json();

    if (result.success) {
      console.log('Image generated:', result.image_url);

      // Close WebSocket after generation
      ws.close();

      return result;
    } else {
      throw new Error(result.message);
    }
  } catch (error) {
    console.error('Generation failed:', error);
    ws.close();
    throw error;
  }
};
```

### 3. Update UI Components

```javascript
// Progress Bar Component
const ProgressBar = ({ percentage }) => {
  return (
    <div className="progress-container">
      <div className="progress-fill" style={{ width: `${percentage}%` }}>
        <span className="progress-text">{Math.round(percentage)}%</span>
      </div>
    </div>
  );
};

// Progress Details Component
const ProgressDetails = ({
  currentStep,
  totalSteps,
  stage,
  elapsedTime,
  remainingTime,
}) => {
  const formatTime = (seconds) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
  };

  return (
    <div className="progress-details">
      <div>
        Step: {currentStep}/{totalSteps}
      </div>
      <div>Stage: {stage === 'base_model' ? 'Base' : 'Refinement'}</div>
      <div>Elapsed: {formatTime(elapsedTime)}</div>
      <div>Remaining: {formatTime(remainingTime)}</div>
    </div>
  );
};
```

### 4. Example: Complete Image Generation Modal

```javascript
import React, { useState, useEffect } from 'react';

export const ImageGenerationModal = ({ taskTopic, onClose }) => {
  const [progress, setProgress] = useState(null);
  const [isGenerating, setIsGenerating] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const startGeneration = async () => {
      const taskId = `task-${Date.now()}`;

      const ws = new WebSocket(
        `ws://localhost:8000/ws/image-generation/${taskId}`
      );

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'progress') {
          setProgress(data);
        }
      };

      ws.onerror = () => {
        setError('Connection error');
        setIsGenerating(false);
      };

      try {
        const response = await fetch(
          'http://localhost:8000/api/media/generate',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${getAuthToken()}`,
            },
            body: JSON.stringify({
              prompt: taskTopic,
              use_generation: true,
              task_id: taskId,
            }),
          }
        );

        const result = await response.json();

        if (result.success) {
          setProgress({
            percentage: 100,
            status: 'completed',
            message: 'Image generated successfully',
          });
          setIsGenerating(false);
        } else {
          setError(result.message);
          setIsGenerating(false);
        }

        ws.close();
      } catch (err) {
        setError(err.message);
        setIsGenerating(false);
        ws.close();
      }
    };

    startGeneration();
  }, []);

  return (
    <div className="modal">
      <div className="modal-content">
        <h2>Generating Featured Image</h2>

        {isGenerating && progress ? (
          <div className="generation-progress">
            <ProgressBar percentage={progress.percentage} />

            <div className="progress-info">
              <p>
                Step: {progress.current_step}/{progress.total_steps}
              </p>
              <p>Stage: {progress.current_stage}</p>
              <p>Elapsed: {Math.round(progress.elapsed_time)}s</p>
              <p>Remaining: {Math.round(progress.estimated_remaining)}s</p>
            </div>
          </div>
        ) : error ? (
          <div className="error-message">{error}</div>
        ) : (
          <div className="success-message">Image generated successfully!</div>
        )}

        <button onClick={onClose} disabled={isGenerating}>
          {isGenerating ? 'Generating...' : 'Close'}
        </button>
      </div>
    </div>
  );
};
```

## CSS Styling

```css
.progress-container {
  width: 100%;
  height: 30px;
  background-color: #f0f0f0;
  border-radius: 4px;
  overflow: hidden;
  margin: 20px 0;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #4caf50, #45a049);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.3s ease;
  color: white;
  font-weight: bold;
  font-size: 12px;
}

.progress-details {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 15px;
  padding: 10px;
  background-color: #f9f9f9;
  border-radius: 4px;
}

.progress-details div {
  font-size: 14px;
  color: #666;
}
```

## API Integration Checklist

- [ ] Add `task_id` parameter to image generation request
- [ ] Store `task_id` in component state during generation
- [ ] Create WebSocket connection with task ID
- [ ] Update UI on each progress message
- [ ] Handle WebSocket disconnect
- [ ] Show error message if connection fails
- [ ] Close WebSocket when generation completes
- [ ] Format elapsed/remaining time nicely
- [ ] Handle edge cases (network interruption, rapid clicks, etc.)

## Message Types Reference

### Progress Update

```json
{
  "type": "progress",
  "task_id": "task-123",
  "status": "generating",
  "current_step": 32,
  "total_steps": 50,
  "percentage": 64.0,
  "current_stage": "base_model", // or "refiner_model"
  "elapsed_time": 46.5,
  "estimated_remaining": 26.3,
  "message": "Base model generation: step 32/50",
  "timestamp": "2025-12-17T05:24:35.123Z"
}
```

### Keep-Alive

```json
{
  "type": "keep-alive"
}
```

### Error

```json
{
  "type": "progress",
  "task_id": "task-123",
  "status": "failed",
  "error": "Out of memory",
  "message": "Generation failed: Out of memory"
}
```

## Performance Notes

- Connection typically established in <100ms
- Progress updates arrive ~1 per second
- Messages are ~200 bytes each
- Keep-alive ping every 30 seconds when idle
- Total bandwidth during generation: ~200-400 bytes/sec

## Troubleshooting

### WebSocket connection fails

- Check that backend is running on port 8000
- Verify CORS is configured correctly
- Check browser console for error messages

### No progress updates

- Verify `task_id` matches between API call and WebSocket connection
- Check that generation actually started (look for "Stage 1" messages)
- Verify WebSocket connection is still open

### Slow updates

- Normal - GPU generation sends update every 1-2 seconds
- CPU generation may have slower updates due to computational overhead

---

**Ready to integrate!** Copy these code snippets into your frontend components.
