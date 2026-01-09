# Writing Style System - UI Integration Implementation Guide

## Overview

This document provides complete instructions for integrating the RAG-based Writing Style System with the Oversight Hub UI and content generation workflows.

## Components Created

### 1. Writing Style Service (`writingStyleService.js`)

**Location:** `web/oversight-hub/src/services/writingStyleService.js`

Provides API client methods for managing writing samples:

- `uploadWritingSample()` - Upload new sample (file or text)
- `getUserWritingSamples()` - Fetch all user samples
- `getActiveWritingSample()` - Get currently active sample
- `setActiveWritingSample()` - Set a sample as active
- `updateWritingSample()` - Edit sample metadata
- `deleteWritingSample()` - Remove a sample

**Usage:**

```javascript
import { uploadWritingSample } from '../services/writingStyleService';

// Upload with file
await uploadWritingSample(title, description, fileObject, true);

// Upload with text content
await uploadWritingSample(title, description, textContent, false);
```

### 2. Writing Style Manager Component (`WritingStyleManager.jsx`)

**Location:** `web/oversight-hub/src/components/WritingStyleManager.jsx`

Full-featured component for managing writing samples. Features:

- Upload new samples via file or text paste
- View all samples with metadata (word count, last updated)
- Edit sample titles and descriptions
- Set active sample (highlighted with blue border)
- Delete samples with confirmation
- Visual feedback (loading states, alerts, success messages)
- Material-UI design with responsive layout

**Props:** None (standalone component)

**Usage in Settings page:**

```javascript
<WritingStyleManager />
```

### 3. Writing Style Selector Component (`WritingStyleSelector.jsx`)

**Location:** `web/oversight-hub/src/components/WritingStyleSelector.jsx`

Form control for selecting writing style in content creation forms.

**Features:**

- Dropdown list of available samples
- Shows "Active" badge on the currently active sample
- Auto-selects active sample on first load
- Graceful handling of no samples scenario
- Helpful error messages and loading states

**Props:**

```javascript
{
  value: string,              // Selected sample ID
  onChange: function,         // Callback when selection changes
  required: boolean,          // Field is required (default: false)
  variant: string,           // MUI variant (default: 'outlined')
  disabled: boolean,         // Disable the field (default: false)
  includeNone: boolean       // Show "None" option (default: true)
}
```

**Usage in task creation:**

```javascript
import WritingStyleSelector from '../components/WritingStyleSelector';

const [selectedStyleId, setSelectedStyleId] = useState('');

<WritingStyleSelector
  value={selectedStyleId}
  onChange={setSelectedStyleId}
  variant="outlined"
/>;
```

## Integration Steps

### Step 1: Add Writing Style Manager to Settings Page

✅ **Already completed**

The `Settings.jsx` page now includes the WritingStyleManager component:

```javascript
import WritingStyleManager from '../components/WritingStyleManager';

// In return statement:
<Container maxWidth="md" sx={{ py: 3 }}>
  <WritingStyleManager />
</Container>;
```

### Step 2: Add Writing Style Selector to Task Creation Modal

Find your task creation modal (likely `CreateTaskModal.jsx` or similar):

```javascript
import WritingStyleSelector from '../components/WritingStyleSelector';

function CreateTaskModal() {
  const [writingStyleId, setWritingStyleId] = useState('');

  // ... other state

  return (
    <Dialog open={open} onClose={handleClose}>
      {/* ... other fields ... */}

      <WritingStyleSelector
        value={writingStyleId}
        onChange={setWritingStyleId}
        variant="outlined"
      />

      {/* ... rest of form ... */}
    </Dialog>
  );
}
```

### Step 3: Send Writing Style ID with Task Creation

When creating a task, include the writing style ID in the request:

```javascript
const taskPayload = {
  title: taskTitle,
  description: taskDescription,
  type: 'content_generation',
  writing_style_id: writingStyleId, // Add this field
  // ... other fields
};

await createTask(taskPayload);
```

### Step 4: Update Content Agent to Use Writing Style

The Python backend should:

1. Retrieve the writing sample when executing a task
2. Pass it to the RAG retriever during content generation
3. Include relevant style passages in the prompt

**Backend implementation (in `src/cofounder_agent/services/writing_style_service.py`):**

```python
class WritingStyleService:
    async def get_writing_sample(self, user_id: str, sample_id: str):
        """Retrieve a writing sample for style matching"""
        # Query database for sample
        sample = await self.db.query(WritingSample).filter(
            WritingSample.id == sample_id,
            WritingSample.user_id == user_id
        ).first()
        return sample

    async def embed_writing_sample(self, sample: WritingSample):
        """Generate embeddings for RAG retrieval"""
        # Break sample into chunks
        # Generate embeddings for each chunk
        # Store in vector database
        pass
```

## Backend Integration Checklist

- [ ] Create `/api/writing-style/*` endpoints in FastAPI
- [ ] Implement PostgreSQL schema for writing_samples table
- [ ] Create WritingStyleService in services/
- [ ] Add vector embeddings for RAG retrieval
- [ ] Update content agent to retrieve and use samples
- [ ] Add authentication/user isolation
- [ ] Add file upload handling (TXT, MD, PDF parsing)
- [ ] Add background job for embedding generation

## Database Schema Reference

```sql
CREATE TABLE writing_samples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    file_path VARCHAR(512),
    word_count INTEGER,
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, title)
);

CREATE TABLE writing_sample_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id UUID NOT NULL REFERENCES writing_samples(id) ON DELETE CASCADE,
    chunk_index INTEGER,
    chunk_text TEXT NOT NULL,
    embedding vector(1536),  -- pgvector extension required
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Testing the Integration

### Manual Testing

1. **Upload Sample:**
   - Navigate to Settings
   - Click "Upload Sample"
   - Upload a text file or paste content
   - Verify it appears in the list

2. **Set Active:**
   - Click "Set Active" on a sample
   - Verify blue border and "Active" badge appears
   - Refresh page and verify persistence

3. **Create Task:**
   - Create a new task
   - Select writing style from dropdown
   - Verify selection persists in form
   - Submit task

4. **Task Execution:**
   - Monitor task progress
   - Verify generated content matches selected style

### API Testing

```bash
# Upload sample
curl -X POST http://localhost:8000/api/writing-style/upload \
  -F "title=Blog Post Style" \
  -F "description=For marketing blog posts" \
  -F "content=@sample.txt"

# Get samples
curl http://localhost:8000/api/writing-style/samples

# Set active
curl -X PUT http://localhost:8000/api/writing-style/{id}/set-active

# Delete sample
curl -X DELETE http://localhost:8000/api/writing-style/{id}
```

## User Experience Flow

1. **Onboarding:**
   - User opens Settings tab
   - Clicks "Upload Sample" in Writing Style Manager
   - Uploads a writing sample (file or text)
   - System automatically sets it as active if first sample

2. **Task Creation:**
   - User creates new content generation task
   - Selects writing style from dropdown
   - System shows "Active" badge for currently active style
   - Submits task with style reference

3. **Content Generation:**
   - Content agent retrieves writing sample
   - RAG system finds relevant style passages
   - Style guidance included in prompt
   - Generated content matches user's writing voice

4. **Management:**
   - User can upload multiple samples for different styles
   - Switch active sample anytime
   - Edit sample titles/descriptions
   - Delete unused samples

## Styling & Customization

### Material-UI Customization

Components use Material-UI (MUI) for consistent styling. To customize:

**Theme colors:**

```javascript
// In theme configuration
{
  primary: { main: '#1976d2' },
  success: { main: '#4caf50' },
  warning: { main: '#ff9800' },
  error: { main: '#f44336' }
}
```

**Component overrides:**

```javascript
// Card styling
<Card
  sx={{
    boxShadow: 2,
    '&:hover': { boxShadow: 4 },
    borderRadius: 2,
  }}
/>
```

### CSS for Settings Page

If keeping the existing CSS, ensure compatibility:

```css
.settings-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
}

/* Ensure MUI components have proper spacing */
.settings-container > * {
  margin-bottom: 24px;
}
```

## Error Handling & Edge Cases

### Handled Scenarios

- ✅ No writing samples uploaded
- ✅ File size validation (max 1MB)
- ✅ File type validation (TXT, MD, PDF)
- ✅ Network errors with user feedback
- ✅ Duplicate sample titles
- ✅ Concurrent operations (uploading while editing)

### Future Enhancements

- Sample preview modal (show full content)
- Batch upload multiple samples
- Sample categories/tagging
- Style comparison tool
- Sample import from templates
- Writing analytics (style metrics)

## Performance Considerations

- **Lazy loading:** Writing samples loaded only when Settings viewed
- **Caching:** Active sample cached in state to reduce API calls
- **Pagination:** Large sample lists paginated (future)
- **Vector search:** Embeddings indexed for fast RAG retrieval

## Security Considerations

- User-isolated data (can only access own samples)
- File upload validation (size, type, content scanning)
- Secure file storage (encrypted at rest)
- CORS protection on API endpoints
- Rate limiting on file uploads

## Next Steps

1. **Backend Implementation:**
   - Create Flask/FastAPI endpoints for writing style CRUD
   - Implement PostgreSQL schema and migrations
   - Add file upload handling and parsing
   - Set up vector embeddings for RAG

2. **Integration:**
   - Connect WritingStyleSelector to task creation flow
   - Update content agent to retrieve and use samples
   - Test end-to-end content generation with styles

3. **Refinement:**
   - Gather user feedback on UX
   - Optimize retrieval performance
   - Add advanced features (sample comparison, analytics)

4. **Documentation:**
   - Create user guide for uploading samples
   - Document best practices for writing samples
   - Create video tutorial (optional)

## Support & Troubleshooting

### Common Issues

**Q: "No writing samples available" message**

- Solution: Upload at least one sample in Settings

**Q: Writing style not affecting content**

- Solution: Verify active sample is set correctly
- Check backend is retrieving and using the sample
- Review RAG retrieval implementation

**Q: File upload fails**

- Solution: Ensure file is TXT, MD, or PDF format
- Check file size is under 1MB
- Verify network connectivity

**Q: Component not rendering in Settings**

- Solution: Ensure WritingStyleManager import is correct
- Check Material-UI components are available
- Review browser console for errors

## References

- [Material-UI Documentation](https://mui.com/material-ui/getting-started/)
- [RAG Implementation Guide](../05-AI_AGENTS_AND_INTEGRATION.md)
- [Writing Style System Architecture](./WRITING_STYLE_SYSTEM.md)
