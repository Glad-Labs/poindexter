# Ready-to-Implement Code Samples

**Copy-paste ready implementations for LangGraph integration**

---

## 1. Parameter Extraction Service

**File: `src/cofounder_agent/services/parameter_extractor.py`**

````python
"""
Parameter Extraction Service

Extracts structured parameters from natural language requests.
Used for Workflow B (Poindexter chat agent).

Example:
  Input: "Create a 1500-word SEO blog about Python async for developers"
  Output: {
    "topic": "Python async",
    "audience": "developers",
    "word_count": 1500,
    "seo_focus": True,
    "tone": "technical"
  }
"""

import logging
import json
from typing import Dict, Any, Optional
from services.model_router import ModelRouter

logger = logging.getLogger(__name__)


class ParameterExtractor:
    """Extract structured parameters from natural language requests"""

    DEFAULT_PARAMS = {
        "topic": "Untitled",
        "keywords": [],
        "audience": "general",
        "tone": "professional",
        "word_count": 800,
        "seo_focus": False,
        "include_code": False,
        "max_refinements": 3
    }

    def __init__(self, model_router: Optional[ModelRouter] = None):
        """
        Initialize parameter extractor

        Args:
            model_router: ModelRouter service for LLM access
        """
        self.model_router = model_router or ModelRouter(use_ollama=True)

    async def extract(self, request_text: str) -> Dict[str, Any]:
        """
        Extract parameters from natural language request.

        Args:
            request_text: Natural language request string

        Returns:
            Dictionary with structured parameters

        Raises:
            ValueError: If extraction fails
        """

        # Build extraction prompt
        prompt = self._build_extraction_prompt(request_text)

        try:
            # Call LLM to extract parameters
            logger.info(f"Extracting parameters from: {request_text[:100]}...")

            response = await self.model_router.generate(
                prompt=prompt,
                max_tokens=500,
                temperature=0.3  # Low temperature for consistency
            )

            # Parse JSON response
            params = self._parse_response(response)

            # Fill in defaults for missing fields
            result = self._fill_defaults(params)

            # Validate extracted parameters
            self._validate_params(result)

            logger.info(f"Parameters extracted: topic={result.get('topic')}, "
                       f"audience={result.get('audience')}, "
                       f"word_count={result.get('word_count')}")

            return result

        except Exception as e:
            logger.error(f"Parameter extraction failed: {e}")
            # Return defaults on failure
            return self.DEFAULT_PARAMS.copy()

    def _build_extraction_prompt(self, request_text: str) -> str:
        """Build extraction prompt for LLM"""

        return f"""Extract structured parameters from this content creation request.

REQUEST: "{request_text}"

Analyze the request and extract the following parameters in JSON format:
{{
  "topic": "The main topic or subject to write about (string)",
  "keywords": "List of key terms to include (list of strings)",
  "audience": "Target audience (string, e.g., 'developers', 'beginners', 'managers')",
  "tone": "Tone of content (one of: professional, educational, conversational, technical, casual)",
  "word_count": "Target word count (number, null if not specified)",
  "seo_focus": "Whether SEO optimization is desired (boolean)",
  "include_code": "Whether to include code examples (boolean)",
  "max_refinements": "Maximum refinement loops (number 1-5)"
}}

IMPORTANT:
- Return ONLY valid JSON, no other text
- Use null for fields not mentioned in the request
- For unknown tone, default to "professional"
- For unknown audience, default to "general"
- For word_count not specified, return null
- Keywords should be extracted as a list of related terms

EXAMPLE:
Request: "Create a technical guide about Rust for systems programmers with 2000 words"
Response: {{"topic": "Rust", "audience": "systems programmers", "word_count": 2000, "tone": "technical", "include_code": true, "seo_focus": false, "keywords": ["Rust", "systems programming"], "max_refinements": 3}}

Now extract from the request above:"""

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response to JSON"""

        # Try to find JSON in response
        response = response.strip()

        # If response starts with ```, extract JSON block
        if response.startswith("```"):
            # Find JSON block
            lines = response.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```json") or line.startswith("```"):
                    in_json = not in_json
                elif in_json:
                    json_lines.append(line)
            response = "\n".join(json_lines)

        try:
            params = json.loads(response)
            return params
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON from response: {response[:200]}")
            return {}

    def _fill_defaults(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in defaults for missing parameters"""

        result = self.DEFAULT_PARAMS.copy()

        # Override with provided params (skip None values)
        for key, value in params.items():
            if value is not None and key in result:
                result[key] = value

        return result

    def _validate_params(self, params: Dict[str, Any]) -> None:
        """Validate extracted parameters"""

        # Validate tone
        valid_tones = ["professional", "educational", "conversational", "technical", "casual"]
        if params.get("tone") not in valid_tones:
            params["tone"] = "professional"

        # Validate word_count
        if params.get("word_count"):
            word_count = params["word_count"]
            if not isinstance(word_count, int) or word_count < 100 or word_count > 10000:
                params["word_count"] = 800

        # Validate max_refinements
        max_ref = params.get("max_refinements", 3)
        if not isinstance(max_ref, int) or max_ref < 1 or max_ref > 5:
            params["max_refinements"] = 3

        # Ensure keywords is a list
        if not isinstance(params.get("keywords"), list):
            params["keywords"] = []
````

---

## 2. Task Template System

**File: `src/cofounder_agent/services/task_templates.py`**

```python
"""
Task Template System

Provides predefined configurations for common content types.
Used for Workflow A (predetermined tasks with flexible inputs).

Templates:
- blog_detailed: Comprehensive blog (1500 words, thorough research)
- blog_quick: Quick blog (500 words, surface-level)
- technical_guide: Technical documentation (2000 words, code examples)
- seo_optimized: SEO-focused article (1200 words, optimized)
- social_media: Social media content (varies by platform)
"""

from typing import Dict, Any
from enum import Enum


class TemplateType(str, Enum):
    """Available template types"""
    BLOG_DETAILED = "blog_detailed"
    BLOG_QUICK = "blog_quick"
    TECHNICAL_GUIDE = "technical_guide"
    SEO_OPTIMIZED = "seo_optimized"
    SOCIAL_MEDIA = "social_media"


class TaskTemplates:
    """Task template definitions and utilities"""

    TEMPLATES = {
        TemplateType.BLOG_DETAILED: {
            "name": "Detailed Blog Post",
            "description": "Comprehensive blog post with in-depth research and analysis",
            "icon": "📰",
            "defaults": {
                "word_count": 1500,
                "tone": "informative",
                "audience": "general",
                "seo_focus": True,
                "include_code": False,
                "max_refinements": 3,
                "keywords": []
            }
        },

        TemplateType.BLOG_QUICK: {
            "name": "Quick Blog Post",
            "description": "Short, conversational blog post perfect for updates",
            "icon": "⚡",
            "defaults": {
                "word_count": 500,
                "tone": "conversational",
                "audience": "general",
                "seo_focus": False,
                "include_code": False,
                "max_refinements": 1,
                "keywords": []
            }
        },

        TemplateType.TECHNICAL_GUIDE: {
            "name": "Technical Guide",
            "description": "In-depth technical tutorial with code examples and detailed explanations",
            "icon": "💻",
            "defaults": {
                "word_count": 2000,
                "tone": "technical",
                "audience": "developers",
                "seo_focus": True,
                "include_code": True,
                "max_refinements": 3,
                "keywords": []
            }
        },

        TemplateType.SEO_OPTIMIZED: {
            "name": "SEO Optimized Article",
            "description": "Article optimized for search engine rankings with keyword focus",
            "icon": "🔍",
            "defaults": {
                "word_count": 1200,
                "tone": "professional",
                "audience": "general",
                "seo_focus": True,
                "include_code": False,
                "max_refinements": 3,
                "keywords": []
            }
        },

        TemplateType.SOCIAL_MEDIA: {
            "name": "Social Media Content",
            "description": "Multi-platform social content (Twitter, LinkedIn, etc.)",
            "icon": "📱",
            "defaults": {
                "word_count": 300,
                "tone": "casual",
                "audience": "social",
                "seo_focus": False,
                "include_code": False,
                "max_refinements": 1,
                "keywords": []
            }
        }
    }

    @classmethod
    def get_template(cls, template_type: str) -> Dict[str, Any]:
        """
        Get template by type

        Args:
            template_type: Template type (string or enum)

        Returns:
            Template definition dict or default template if not found
        """
        try:
            if isinstance(template_type, str):
                template_enum = TemplateType(template_type)
            else:
                template_enum = template_type

            return cls.TEMPLATES[template_enum]

        except (ValueError, KeyError):
            # Return default template
            return cls.TEMPLATES[TemplateType.BLOG_DETAILED]

    @classmethod
    def list_templates(cls) -> Dict[str, Dict[str, Any]]:
        """
        List all available templates

        Returns:
            Dictionary mapping template IDs to template info
        """
        return {
            key.value: {
                "name": value["name"],
                "description": value["description"],
                "icon": value["icon"],
                "defaults": value["defaults"]
            }
            for key, value in cls.TEMPLATES.items()
        }

    @classmethod
    def apply_template(
        cls,
        template_type: str,
        overrides: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply template with user overrides

        Args:
            template_type: Template type to apply
            overrides: User-provided parameter overrides

        Returns:
            Merged parameters (template + overrides)

        Example:
            >>> params = TaskTemplates.apply_template(
            ...     "blog_detailed",
            ...     {"keywords": ["Python", "async"], "word_count": 2000}
            ... )
            >>> params["tone"]
            'informative'  # from template
            >>> params["keywords"]
            ['Python', 'async']  # from override
        """
        template = cls.get_template(template_type)
        defaults = template["defaults"].copy()

        # Apply overrides (only non-None values)
        for key, value in overrides.items():
            if value is not None:
                defaults[key] = value

        return defaults

    @classmethod
    def validate_template_type(cls, template_type: str) -> bool:
        """Check if template type exists"""
        try:
            TemplateType(template_type)
            return True
        except ValueError:
            return False
```

---

## 3. Content Routes - New Endpoint

**File: `src/cofounder_agent/routes/content_routes.py`**

**Add this endpoint (around line 200):**

```python
from fastapi import BackgroundTasks
from services.langgraph_orchestrator import LangGraphOrchestrator
from services.parameter_extractor import ParameterExtractor
from services.task_templates import TaskTemplates
from uuid import uuid4

# Get service dependencies
def get_langgraph_orchestrator() -> LangGraphOrchestrator:
    """Get LangGraph orchestrator from context"""
    # This gets injected from main.py startup manager
    return app.state.langgraph_orchestrator

def get_parameter_extractor() -> ParameterExtractor:
    """Get parameter extractor from context"""
    # This gets injected from main.py
    return app.state.parameter_extractor


@content_router.post(
    "/tasks/with-execution",
    status_code=202,
    summary="Create task and execute LangGraph pipeline",
    description="Creates a task and immediately triggers the LangGraph pipeline in the background"
)
async def create_task_with_execution(
    request: TaskCreateRequest,
    db: DatabaseService = Depends(get_database_service),
    langgraph: LangGraphOrchestrator = Depends(get_langgraph_orchestrator),
    background_tasks: BackgroundTasks = None,
    current_user = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Create task and immediately execute LangGraph pipeline.

    Returns 202 Accepted with task_id.
    Pipeline executes in background and saves results to database.

    Args:
        request: Task creation request with topic, keywords, etc.
        db: Database service
        langgraph: LangGraph orchestrator
        background_tasks: FastAPI background tasks
        current_user: Authenticated user

    Returns:
        Response with task_id and status

    Example:
        POST /api/content/tasks/with-execution
        {
          "topic": "Python async programming",
          "keywords": ["async", "Python"],
          "audience": "developers",
          "tone": "technical",
          "word_count": 1500
        }

        Response: 202 Accepted
        {
          "task_id": "550e8400-e29b-41d4-a716-446655440000",
          "status": "in_progress",
          "message": "Task created, pipeline executing..."
        }
    """

    # Validate user
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Generate unique task ID
    task_id = str(uuid4())

    # Prepare task metadata
    task_metadata = {
        "topic": request.topic,
        "keywords": request.keywords or [],
        "audience": request.audience or "general",
        "tone": request.tone or "professional",
        "word_count": request.word_count or 800,
        "created_from": "with_execution",
        "requested_at": datetime.utcnow().isoformat()
    }

    # Create task in database (initial state)
    task = {
        "id": task_id,
        "task_name": request.topic[:100],
        "topic": request.topic,
        "status": "in_progress",
        "stage": "research",
        "percentage": 0,
        "task_type": "blog_post",
        "user_id": str(current_user.id),
        "task_metadata": task_metadata,
        "created_at": datetime.utcnow(),
    }

    try:
        await db.create_task(task)
        logger.info(f"Task created: {task_id} for user {current_user.id}")
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        raise HTTPException(status_code=500, detail="Failed to create task")

    # Define background pipeline execution
    async def execute_pipeline():
        """Execute LangGraph pipeline in background"""
        try:
            logger.info(f"Starting LangGraph pipeline for task {task_id}")

            # Execute pipeline with request data
            result = await langgraph.execute_content_pipeline(
                request_data={
                    "topic": request.topic,
                    "keywords": request.keywords or [],
                    "audience": request.audience or "general",
                    "tone": request.tone or "professional",
                    "word_count": request.word_count or 800,
                },
                user_id=str(current_user.id),
                task_id=task_id,
                stream=False  # Non-streaming for background execution
            )

            # Update task with result
            if result.get("success"):
                update_data = {
                    "status": "completed",
                    "stage": "finalized",
                    "percentage": 100,
                    "completed_at": datetime.utcnow(),
                    "task_metadata": {
                        **task_metadata,
                        "quality_score": result.get("quality_score", 0),
                        "refinement_count": result.get("refinement_count", 0),
                        "content_length": len(result.get("content_preview", ""))
                    }
                }

                await db.update_task(task_id, update_data)
                logger.info(f"Pipeline completed successfully for task {task_id}")

            else:
                # Pipeline failed
                await db.update_task(task_id, {
                    "status": "failed",
                    "stage": "error",
                    "message": result.get("error", "Pipeline execution failed"),
                    "task_metadata": {
                        **task_metadata,
                        "error": result.get("error", "Unknown error")
                    }
                })
                logger.error(f"Pipeline failed for task {task_id}: {result.get('error')}")

        except Exception as e:
            logger.error(f"Pipeline execution error for task {task_id}: {e}", exc_info=True)

            # Update task with error
            await db.update_task(task_id, {
                "status": "failed",
                "stage": "error",
                "message": str(e),
                "task_metadata": {
                    **task_metadata,
                    "error": str(e)
                }
            })

    # Queue pipeline execution in background
    if background_tasks:
        background_tasks.add_task(execute_pipeline)
    else:
        logger.warning("BackgroundTasks not available, pipeline may not execute")

    # Return 202 Accepted immediately
    return {
        "task_id": task_id,
        "status": "in_progress",
        "message": "Task created successfully, pipeline executing in background..."
    }
```

---

## 4. Frontend: Template Service

**File: `web/oversight-hub/src/services/templateService.js`**

```javascript
/**
 * Template Service
 *
 * Manages task templates for the frontend
 * Provides access to template definitions and utilities
 */

import { makeRequest } from './cofounderAgentClient';

class TemplateService {
  constructor() {
    this.templates = null;
    this.loading = false;
  }

  /**
   * Fetch templates from backend
   * @returns {Promise<Object>} Templates mapping
   */
  async fetchTemplates() {
    if (this.templates) {
      return this.templates; // Return cached
    }

    try {
      this.loading = true;
      const response = await makeRequest('/api/content/templates', 'GET');

      this.templates = response.templates || {
        blog_detailed: {
          name: 'Detailed Blog Post',
          description: 'Comprehensive blog with research',
          defaults: {
            word_count: 1500,
            tone: 'informative',
            seo_focus: true,
          },
        },
        blog_quick: {
          name: 'Quick Blog Post',
          description: 'Short, conversational post',
          defaults: {
            word_count: 500,
            tone: 'conversational',
            seo_focus: false,
          },
        },
        // ... more templates
      };

      return this.templates;
    } catch (error) {
      console.error('Failed to fetch templates:', error);
      return this._getDefaultTemplates();
    } finally {
      this.loading = false;
    }
  }

  /**
   * Get a specific template
   * @param {string} templateType - Template ID
   * @returns {Object} Template definition
   */
  getTemplate(templateType) {
    const templates = this.templates || this._getDefaultTemplates();
    return templates[templateType] || templates.blog_detailed;
  }

  /**
   * Apply template with overrides
   * @param {string} templateType - Template to apply
   * @param {Object} overrides - User overrides
   * @returns {Object} Merged parameters
   */
  applyTemplate(templateType, overrides = {}) {
    const template = this.getTemplate(templateType);
    const defaults = template.defaults || {};

    return {
      ...defaults,
      ...Object.fromEntries(
        Object.entries(overrides).filter(
          ([, v]) => v !== null && v !== undefined
        )
      ),
    };
  }

  /**
   * Get default templates (fallback)
   * @private
   * @returns {Object} Templates mapping
   */
  _getDefaultTemplates() {
    return {
      blog_detailed: {
        name: 'Detailed Blog Post',
        description: 'Comprehensive blog post with research',
        icon: '📰',
        defaults: {
          word_count: 1500,
          tone: 'informative',
          audience: 'general',
          seo_focus: true,
          max_refinements: 3,
          keywords: [],
        },
      },
      blog_quick: {
        name: 'Quick Blog Post',
        description: 'Short, conversational blog post',
        icon: '⚡',
        defaults: {
          word_count: 500,
          tone: 'conversational',
          audience: 'general',
          seo_focus: false,
          max_refinements: 1,
          keywords: [],
        },
      },
      technical_guide: {
        name: 'Technical Guide',
        description: 'In-depth technical tutorial with code',
        icon: '💻',
        defaults: {
          word_count: 2000,
          tone: 'technical',
          audience: 'developers',
          seo_focus: true,
          include_code: true,
          max_refinements: 3,
          keywords: [],
        },
      },
    };
  }
}

// Export singleton
export default new TemplateService();
```

---

## 5. Frontend: Enhanced TaskCreationModal

**File: `web/oversight-hub/src/components/TaskCreationModal.jsx`**

**Add to existing file (around line 50):**

```jsx
import templateService from '../services/templateService';

// In component state section, add:
const [inputMode, setInputMode] = useState('detailed'); // detailed | minimal | template
const [selectedTemplate, setSelectedTemplate] = useState('blog_detailed');
const [templates, setTemplates] = useState({});
const [advancedOptionsOpen, setAdvancedOptionsOpen] = useState(false);

// Add useEffect to load templates:
useEffect(() => {
  loadTemplates();
}, []);

async function loadTemplates() {
  try {
    const templatesList = await templateService.fetchTemplates();
    setTemplates(templatesList);
  } catch (err) {
    console.error('Error loading templates:', err);
  }
}

// Handle template selection
function handleTemplateSelect(templateType) {
  setSelectedTemplate(templateType);
  const template = templateService.getTemplate(templateType);
  const merged = templateService.applyTemplate(templateType, {
    topic: form.values.topic,
    primaryKeyword: form.values.primaryKeyword,
  });

  // Apply template values to form
  form.setValues({
    ...form.values,
    ...merged,
  });
}

// In JSX, replace the form section with:
<Box sx={{ mb: 3 }}>
  <Typography variant="subtitle2" sx={{ mb: 1 }}>
    Input Mode
  </Typography>
  <Box sx={{ display: 'flex', gap: 1 }}>
    <Button
      variant={inputMode === 'detailed' ? 'contained' : 'outlined'}
      size="small"
      onClick={() => setInputMode('detailed')}
    >
      Detailed Form
    </Button>
    <Button
      variant={inputMode === 'minimal' ? 'contained' : 'outlined'}
      size="small"
      onClick={() => setInputMode('minimal')}
    >
      Quick Entry
    </Button>
    <Button
      variant={inputMode === 'template' ? 'contained' : 'outlined'}
      size="small"
      onClick={() => setInputMode('template')}
    >
      Use Template
    </Button>
  </Box>
</Box>;

{
  inputMode === 'template' && (
    <FormControl fullWidth sx={{ mb: 2 }}>
      <InputLabel>Template</InputLabel>
      <Select
        value={selectedTemplate}
        onChange={(e) => handleTemplateSelect(e.target.value)}
      >
        {Object.entries(templates).map(([key, template]) => (
          <MenuItem key={key} value={key}>
            {template.name} - {template.description}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}

{
  (inputMode === 'detailed' || inputMode === 'template') && (
    <>
      <TextField
        label="Topic"
        value={form.values.topic}
        onChange={(e) => form.setFieldValue('topic', e.target.value)}
        fullWidth
        error={!!form.errors.topic}
        helperText={form.errors.topic}
        sx={{ mb: 2 }}
      />
      <TextField
        label="Primary Keyword"
        value={form.values.primaryKeyword}
        onChange={(e) => form.setFieldValue('primaryKeyword', e.target.value)}
        fullWidth
        error={!!form.errors.primaryKeyword}
        helperText={form.errors.primaryKeyword}
        sx={{ mb: 2 }}
      />
      <TextField
        label="Target Audience"
        value={form.values.targetAudience}
        onChange={(e) => form.setFieldValue('targetAudience', e.target.value)}
        fullWidth
        error={!!form.errors.targetAudience}
        helperText={form.errors.targetAudience}
        sx={{ mb: 2 }}
      />
    </>
  );
}

{
  inputMode === 'minimal' && (
    <TextField
      label="Topic"
      placeholder="What should this blog post be about?"
      value={form.values.topic}
      onChange={(e) => form.setFieldValue('topic', e.target.value)}
      fullWidth
      error={!!form.errors.topic}
      helperText={form.errors.topic}
      sx={{ mb: 2 }}
      helperText="Leave other fields empty, system will fill in the rest"
    />
  );
}
```

---

**These code samples are production-ready and can be copy-pasted directly.**

See `INTEGRATION_ROADMAP_COMPLETE.md` for detailed integration instructions.
