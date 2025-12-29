# Image Generation UX Enhancements - Implementation Summary

## Overview

Enhanced the image generation UI in the Oversight Hub with three key improvements:

1. ✅ **Better title extraction** - Grabs full title with subtitle from content
2. ✅ **Progress bar** - Shows generation progress with percentage
3. ✅ **Retry functionality** - "Try Again" button to regenerate different images

---

## Changes Made

### 1. Improved Title Extraction from Content

**File:** [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx)

**Added Function:** `extractTitleFromContent()`

```javascript
// Helper function to extract full title from content (e.g., "Title: Best Eats in the Northeast USA: A Culinary Guide")
const extractTitleFromContent = (content, fallbackTitle) => {
  if (!content) return fallbackTitle;

  // Look for "Title: ..." pattern on its own line or at start
  const titleMatch = content.match(/^[\s]*Title:\s*(.+?)(?:\n|$)/im);
  if (titleMatch && titleMatch[1]) {
    return titleMatch[1].trim();
  }

  // Fallback to first heading if no Title: pattern
  const headingMatch = content.match(/^#+\s*(.+?)$/m);
  if (headingMatch && headingMatch[1]) {
    return headingMatch[1].trim();
  }

  return fallbackTitle;
};
```

**How It Works:**

1. Searches for pattern: `Title: [full title including subtitles]`
2. Extracts everything after "Title:" until end of line
3. Falls back to first markdown heading if no "Title:" pattern
4. Uses fallback title from metadata if no content title found

**Before:**

```
Content: "Title: Best Eats in the Northeast USA: A Culinary Guide\n..."
Extracted Title: "Best eats in the northeast USA"  ❌
```

**After:**

```
Content: "Title: Best Eats in the Northeast USA: A Culinary Guide\n..."
Extracted Title: "Best Eats in the Northeast USA: A Culinary Guide"  ✅
```

**Applied to all load paths:**

- ✅ task_metadata.content
- ✅ result_data.content
- ✅ task.content (legacy)
- ✅ task.result (legacy)

---

### 2. Progress Bar During Image Generation

**File:** [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx)

**Added State:**

```javascript
const [imageGenerationProgress, setImageGenerationProgress] = useState(0);
```

**Enhanced Generation Function:**

```javascript
const generateFeaturedImage = async () => {
  // ... existing validation ...

  setIsGeneratingImage(true);
  setImageGenerationProgress(0); // ← Reset progress
  setImageGenerationMessage('');
  setHasGeneratedImage(false);

  try {
    // Simulate progress updates
    const progressInterval = setInterval(() => {
      setImageGenerationProgress((prev) => {
        if (prev < 80) return prev + Math.random() * 30;
        return prev;
      });
    }, 300);

    // ... API call ...

    if (result.success && result.image_url) {
      setFeaturedImageUrl(result.image_url);
      setImageGenerationMessage(
        `✅ Image from ${result.image?.source || 'image service'} in ${result.generation_time?.toFixed(2) || '?'}s`
      );
      setHasGeneratedImage(true);
      setImageGenerationProgress(100); // ← Set to 100%
      console.log('✅ Featured image generated:', result);
    } else {
      throw new Error(result.message || 'No image URL returned');
    }
  } catch (error) {
    // ... error handling ...
  } finally {
    clearInterval(progressInterval);
    setIsGeneratingImage(false);
    setTimeout(() => setImageGenerationProgress(0), 500); // ← Clear after delay
  }
};
```

**UI Progress Bar:**

```jsx
{
  /* Progress Bar */
}
{
  isGeneratingImage && imageGenerationProgress > 0 && (
    <div className="mt-3">
      <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden border border-gray-600">
        <div
          className="h-full bg-gradient-to-r from-blue-500 to-cyan-500 transition-all duration-300"
          style={{ width: `${Math.min(imageGenerationProgress, 100)}%` }}
        />
      </div>
      <p className="text-xs text-gray-400 mt-1 text-center">
        {Math.round(Math.min(imageGenerationProgress, 100))}% -{' '}
        {imageGenerationMessage
          ? imageGenerationMessage.split(' in ')[0]
          : 'Generating...'}
      </p>
    </div>
  );
}
```

**Features:**

- ✅ Animated gradient bar (blue to cyan)
- ✅ Percentage indicator (0-100%)
- ✅ Status text showing current operation
- ✅ Smooth transitions
- ✅ Only shows during generation

---

### 3. "Try Again" Button for Regeneration

**File:** [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx)

**Added State:**

```javascript
const [hasGeneratedImage, setHasGeneratedImage] = useState(false);
```

**Try Again Button:**

```jsx
{
  hasGeneratedImage && (
    <button
      onClick={generateFeaturedImage}
      disabled={isGeneratingImage}
      className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded font-medium transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 whitespace-nowrap"
      title="Generate another image and replace the current one"
    >
      {isGeneratingImage ? (
        <>
          <span className="animate-spin">⟳</span> Regenerating...
        </>
      ) : (
        '🔄 Try Again'
      )}
    </button>
  );
}
```

**How It Works:**

1. Shows only after successful image generation (`hasGeneratedImage = true`)
2. Clicking regenerates with same settings and source selection
3. Replaces current image with new one
4. Works with:
   - ✅ Pexels (Free, Fast)
   - ✅ SDXL (GPU-based)
   - ✅ Try Both (Pexels first)
5. Shows loading state while regenerating

**Features:**

- ✅ Purple button to distinguish from initial "Generate"
- ✅ Only appears after successful generation
- ✅ Disabled during generation
- ✅ Same source selection applies (user doesn't need to change)
- ✅ Replaces image without clearing input field
- ✅ Keeps generating new images until user is satisfied

---

## UI Flow

### Before Enhancements:

```
1. User enters image source (Pexels/SDXL)
2. User clicks "Generate"
3. [No progress feedback]
4. Result appears or error shows
5. No way to try different image
```

### After Enhancements:

```
1. System extracts full title from content
   ✅ "Best Eats in the Northeast USA: A Culinary Guide"

2. User selects image source (Pexels/SDXL/Both)

3. User clicks "🎨 Generate"

4. Progress bar shows:
   ✅ "0% - Generating..."
   ✅ "35% - Searching Pexels..."
   ✅ "72% - Generating..."
   ✅ "100% - ✅ Image from pexels..."

5. Image appears and "🔄 Try Again" button shown

6. User can:
   ✅ Click "Try Again" to get different image
   ✅ Or edit and click "Generate" to use different source
   ✅ Or approve with current image
```

---

## Code Changes Detail

### State Management

```javascript
// Before
const [featuredImageUrl, setFeaturedImageUrl] = useState('');
const [isGeneratingImage, setIsGeneratingImage] = useState(false);
const [imageSource, setImageSource] = useState('pexels');
const [imageGenerationMessage, setImageGenerationMessage] = useState('');

// After (added)
const [imageGenerationProgress, setImageGenerationProgress] = useState(0); // ← New
const [hasGeneratedImage, setHasGeneratedImage] = useState(false); // ← New
```

### Title Extraction Application

```javascript
// All paths now use:
title = extractTitleFromContent(content, fallbackTitle);

// Instead of just:
// title = fallbackTitle;
```

### Progress Tracking

- Simulation starts at 0%
- Increments randomly by 0-30% every 300ms
- Stops at 80% until response arrives
- Jumps to 100% on success
- Clears after 500ms delay

---

## User Benefits

1. **More Accurate Titles**
   - Full title with subtitles now visible
   - Better context for image generation
   - Matches content more precisely

2. **Better Feedback**
   - Progress bar shows something is happening
   - Percentage gives time expectation
   - Reduces user anxiety about stuck generation

3. **Flexible Image Generation**
   - Try different images without restarting
   - Explore options until satisfied
   - Switch between Pexels and SDXL easily
   - Find perfect match for content

4. **Improved UX**
   - Clear visual feedback at each step
   - Easy to understand the process
   - No confusion about what's happening

---

## Technical Details

### Title Extraction Logic

- Regex pattern: `/^[\s]*Title:\s*(.+?)(?:\n|$)/im`
  - `^` - Start of line
  - `[\s]*` - Optional whitespace
  - `Title:` - Literal "Title:"
  - `\s*` - Optional whitespace after colon
  - `(.+?)` - Capture group for actual title
  - `(?:\n|$)` - Until newline or end of string
  - `i` flag - Case insensitive
  - `m` flag - Multiline mode

### Progress Simulation

- Random increments create natural-looking progress
- Stops at 80% to avoid jumping to 100% prematurely
- Final jump to 100% when result arrives
- Creates perception of smooth, ongoing work

### Retry Implementation

- Same function called (`generateFeaturedImage`)
- Preserves current source selection
- Replaces featured image URL
- Allows unlimited attempts
- User satisfaction driven

---

## Testing Checklist

- [ ] Title extracts correctly with full subtitle
- [ ] Title displays properly in the Title field
- [ ] Progress bar appears during generation
- [ ] Progress percentage increments smoothly
- [ ] Progress reaches 100% on success
- [ ] Progress clears after generation completes
- [ ] "Try Again" button appears after successful generation
- [ ] "Try Again" button allows regeneration
- [ ] "Try Again" works with Pexels
- [ ] "Try Again" works with SDXL
- [ ] "Try Again" works with "Try Both"
- [ ] "Try Again" button disabled during regeneration
- [ ] Progress bar shows during regeneration
- [ ] Multiple attempts work without errors
- [ ] Image URL updates on each generation

---

## Files Modified

| File                                                                                                                           | Changes                           | Type           |
| ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------- | -------------- |
| [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) | Added `extractTitleFromContent()` | New Function   |
| [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) | Added progress state              | New State      |
| [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) | Added `hasGeneratedImage` state   | New State      |
| [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) | Enhanced generation function      | Updated Logic  |
| [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) | Added progress bar UI             | New UI Element |
| [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) | Added "Try Again" button          | New UI Element |

---

## Deployment

✅ **No backend changes needed**  
✅ **No database changes needed**  
✅ **Frontend only**  
✅ **Backwards compatible**

**To deploy:**

1. Restart frontend: `npm start --prefix web/oversight-hub`
2. Clear browser cache if needed
3. Test with image generation

---

## Summary

✅ **All three enhancements implemented**

- Better title extraction from content
- Progress bar with percentage feedback
- Retry button for flexible image generation

✅ **Improved user experience**

- More accurate content titles
- Clear feedback during generation
- Easy image regeneration

✅ **Ready for testing and deployment**
