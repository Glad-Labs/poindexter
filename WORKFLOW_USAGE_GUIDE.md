# Workflow System - Complete Guide

## ✅ Just Fixed

- **Backend validation error**: Now properly handles phases as dictionaries
- **Marketplace navigation**: Fixed `/marketplace` link

## How to Use Workflows

### Step 1: Create a Workflow

1. Go to **<http://localhost:3001/services>**
2. Click the **"Create Custom Workflow"** tab
3. Enter:
   - **Workflow Name** (e.g., "Blog Post Generator")
   - **Description** (e.g., "Generates blog posts on any topic")

### Step 2: Add Phases to Your Workflow

1. In the right panel, you'll see available phases:
   - `research` → Research a topic
   - `draft` → Create initial content
   - `assess` → Evaluate quality
   - `refine` → Improve content
   - `image` → Add images
   - `publish` → Format for publication

2. Click any phase name to add it to your workflow

### Step 3: Configure Each Phase with Inputs

1. **Click on any phase you've added** (in the canvas, NOT in the list)
2. You'll see a config panel on the right with:
   - Phase name (readonly)
   - Agent selection
   - **Phase Inputs** section ← THIS IS WHERE YOU SET TOPICS!

3. For example, in the `research` phase, you'll see:
   - `topic` - **Enter what you want to research**
   - `focus` - (Optional) Specific areas to focus on

4. For the `draft` phase:
   - `prompt` - **Instructions for creating the draft**
   - `content` - Source content to build upon
   - `target_audience` - Who this is for
   - `tone` - Tone/style

### Step 4: Save Your Workflow

1. Click the blue **"Save"** button
2. Your workflow is now saved and can be executed

### Step 5: Execute Your Workflow

1. Click the **"Execute"** button
2. The workflow will:
   - Run the research phase (using YOUR topic)
   - Generate draft content
   - Evaluate quality
   - Refine if needed
   - Find/create images
   - Format for publication

3. View results in the **"Execution History"** tab

---

## Input Fields by Phase

### Research Phase

| Input | Example | Required |
| --- | --- | --- |
| `topic` | "Artificial Intelligence in Healthcare" | Yes |
| `focus` | "Focus on diagnosis accuracy" | No |

### Draft Phase

| Input | Example | Required |
| --- | --- | --- |
| `prompt` | "Write a professional blog post" | Yes |
| `content` | Previous research findings | No |
| `target_audience` | "Healthcare professionals" | No |
| `tone` | "Professional, informative" | No |

### Assess Phase

| Input | Example | Required |
| --- | --- | --- |
| `content` | Auto-populated from draft | No |
| `criteria` | "Check for accuracy" | No |
| `quality_threshold` | "0.7" (70% quality) | No |

### Refine Phase

| Input | Example | Required |
| --- | --- | --- |
| `content` | Auto-populated from draft | No |
| `feedback` | Auto-populated from assessment | No |
| `revision_instructions` | "Make more concise" | No |

### Image Phase

| Input | Example | Required |
| --- | --- | --- |
| `topic` | Auto-populated from research | No |
| `prompt` | "Professional infographic" | No |
| `style` | "Modern, minimalist" | No |

### Publish Phase

| Input | Example | Required |
| --- | --- | --- |
| `content` | Auto-populated from refined content | No |
| `title` | Auto-generated | No |
| `target` | "blog", "social", "email" | No |
| `slug` | "ai-healthcare-2026" | No |
| `tags` | "AI, Healthcare, Technology" | No |

---

## UI Workflow

```
┌─────────────────────────────────────┐
│  Available Phases (Left Panel)       │
│  - research                         │
│  - draft                            │ ← Click phase to add
│  - assess                           │
│  - refine                           │
│  - image                            │
│  - publish                          │
└─────────────────────────────────────┘
              ↓ Click phase
┌─────────────────────────────────────┐
│  Workflow Canvas (Center)            │
│  [research] --→ [draft] --→ [assess] │
│              ↑                       │
│              Click to edit           │
└─────────────────────────────────────┘
              ↓ Click phase box
┌─────────────────────────────────────┐
│  Phase Config (Right Panel)          │
│  ┌─────────────────────────────────┐ │
│  │ Phase: research                 │ │
│  │ Agent: research_agent           │ │
│  │                                 │ │
│  │ Phase Inputs                    │ │
│  │ [topic: ____________]  Required │ │  ← ENTER YOUR TOPIC HERE
│  │ [focus: ____________]  Optional │ │
│  │                                 │ │
│  │ [Save] [Remove]                 │ │
│  └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

---

## Troubleshooting

### "Phase Inputs" section doesn't show?

- Make sure you **clicked on the phase box in the canvas**, not the phase name in the list
- The phase must be added to the workflow first

### Workflow save fails?

- Check console (F12) for error messages
- Ensure all required inputs are filled (marked with *)
- Make sure you have at least one phase

### Workflow execution fails?

- Check that your inputs make sense for the phase (topic should be a real topic, not "test")
- Backend may be slow on first execution (loading AI models)
- Check execution history for error details

---

## Example: Create a "Travel Guide" Workflow

1. Go to /services → "Create Custom Workflow"
2. Name: "Travel Guide Generator"
3. Description: "Creates travel guides for any destination"

4. Add phases in this order:
   - `research` → Topic: "Paris"
   - `draft` → Prompt: "Write a detailed travel guide"
   - `assess` → (uses feedback from draft)
   - `refine` → (improves based on feedback)
   - `image` → (finds travel images)
   - `publish` → Target: "blog"

5. Click each phase and fill in the required inputs
6. Save the workflow
7. Click "Execute" to run it

The system will automatically:

- Research Paris
- Draft a comprehensive guide
- Assess quality
- Refine based on feedback
- Find beautiful travel images
- Format for blog publication

Done!
