"""
Phase Registry - Central registry for all available workflow phases

This module defines the interface for workflow phases and maintains a registry
of all available phases (research, draft, assess, refine, image, publish, etc.)

Phases can be added without modifying the workflow execution engine.
New phases only require:
1. Define a PhaseDefinition with input/output schemas
2. Register it via PhaseRegistry.register_phase()
3. An agent must exist to handle the phase

The registry is extensible and supports dynamic phase addition.
"""

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ContentType(str, Enum):
    """Output content types for mapping purposes"""

    TEXT = "text"
    JSON = "json"
    OBJECT = "object"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"


class InputType(str, Enum):
    """Input field types for UI rendering"""

    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    SELECT = "select"
    BOOLEAN = "boolean"
    EMAIL = "email"


@dataclass
class InputField:
    """Schema definition for a phase input field"""

    key: str
    label: str
    input_type: InputType = InputType.TEXT
    required: bool = False
    default_value: Optional[Any] = None
    description: Optional[str] = None
    placeholder: Optional[str] = None
    options: Optional[List[dict]] = None  # For select inputs
    validation_pattern: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "key": self.key,
            "label": self.label,
            "input_type": self.input_type.value,
            "required": self.required,
            "default_value": self.default_value,
            "description": self.description,
            "placeholder": self.placeholder,
            "options": self.options,
            "validation_pattern": self.validation_pattern,
        }


@dataclass
class OutputField:
    """Schema definition for a phase output field"""

    key: str
    label: str
    content_type: ContentType = ContentType.TEXT
    description: Optional[str] = None
    example: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "key": self.key,
            "label": self.label,
            "content_type": self.content_type.value,
            "description": self.description,
            "example": self.example,
        }


@dataclass
class PhaseDefinition:
    """Complete definition of a workflow phase"""

    name: str
    agent_type: str
    description: str
    input_schema: Dict[str, InputField] = field(default_factory=dict)
    output_schema: Dict[str, OutputField] = field(default_factory=dict)
    required: bool = True
    timeout_seconds: int = 300
    max_retries: int = 3
    skip_on_error: bool = False
    quality_threshold: Optional[float] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "name": self.name,
            "agent_type": self.agent_type,
            "description": self.description,
            "input_schema": {k: v.to_dict() for k, v in self.input_schema.items()},
            "output_schema": {k: v.to_dict() for k, v in self.output_schema.items()},
            "required": self.required,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "skip_on_error": self.skip_on_error,
            "quality_threshold": self.quality_threshold,
            "tags": self.tags,
        }


class PhaseRegistry:
    """
    Central registry for all available workflow phases.

    Singleton pattern: Use PhaseRegistry.get_instance() to access
    """

    _instance: Optional["PhaseRegistry"] = None
    _phases: Dict[str, PhaseDefinition] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._phases = {}
        self._register_builtin_phases()

    @classmethod
    def get_instance(cls) -> "PhaseRegistry":
        """Get the singleton instance"""
        return cls()

    def register_phase(self, phase_def: PhaseDefinition) -> None:
        """Register a new phase definition"""
        if phase_def.name in self._phases:
            logger.warning(f"Phase '{phase_def.name}' already registered, overwriting")
        self._phases[phase_def.name] = phase_def
        logger.info(f"Registered phase: {phase_def.name}")

    def get_phase(self, name: str) -> Optional[PhaseDefinition]:
        """Get a phase definition by name"""
        return self._phases.get(name)

    def list_phases(self) -> List[PhaseDefinition]:
        """List all available phases"""
        return list(self._phases.values())

    def list_phase_names(self) -> List[str]:
        """List all available phase names"""
        return list(self._phases.keys())

    def phase_exists(self, name: str) -> bool:
        """Check if a phase exists"""
        return name in self._phases

    def _register_builtin_phases(self) -> None:
        """Register all built-in workflow phases"""

        # Research Phase
        self.register_phase(
            PhaseDefinition(
                name="research",
                agent_type="research_agent",
                description="Gather background information and research findings",
                input_schema={
                    "topic": InputField(
                        key="topic",
                        label="Research Topic",
                        input_type=InputType.TEXT,
                        required=True,
                        placeholder="e.g., AI and Machine Learning Integration",
                        description="The topic to research",
                    ),
                    "focus": InputField(
                        key="focus",
                        label="Focus Areas",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        placeholder="Optional: specific areas to focus on",
                        description="Specific focus areas for research",
                    ),
                },
                output_schema={
                    "findings": OutputField(
                        key="findings",
                        label="Research Findings",
                        content_type=ContentType.TEXT,
                        description="Comprehensive research findings",
                    ),
                    "sources": OutputField(
                        key="sources",
                        label="Sources",
                        content_type=ContentType.ARRAY,
                        description="References and sources used",
                    ),
                },
                tags=["content-generation", "research"],
            )
        )

        # Draft Phase
        self.register_phase(
            PhaseDefinition(
                name="draft",
                agent_type="creative_agent",
                description="Create initial draft content",
                input_schema={
                    "prompt": InputField(
                        key="prompt",
                        label="Draft Prompt",
                        input_type=InputType.TEXTAREA,
                        required=True,
                        placeholder="Instructions for the draft...",
                        description="Prompt or instructions for creating the draft",
                    ),
                    "content": InputField(
                        key="content",
                        label="Source Content",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        description="Source content to build upon",
                    ),
                    "target_audience": InputField(
                        key="target_audience",
                        label="Target Audience",
                        input_type=InputType.TEXT,
                        required=False,
                        placeholder="e.g., executives, developers",
                        description="Target audience for the content",
                    ),
                    "tone": InputField(
                        key="tone",
                        label="Tone",
                        input_type=InputType.SELECT,
                        required=False,
                        options=[
                            {"label": "Professional", "value": "professional"},
                            {"label": "Casual", "value": "casual"},
                            {"label": "Academic", "value": "academic"},
                            {"label": "Creative", "value": "creative"},
                        ],
                        description="Tone for the content",
                    ),
                },
                output_schema={
                    "draft": OutputField(
                        key="draft",
                        label="Draft Content",
                        content_type=ContentType.TEXT,
                        description="The generated draft",
                    ),
                },
                tags=["content-generation"],
            )
        )

        # Assess Phase
        self.register_phase(
            PhaseDefinition(
                name="assess",
                agent_type="qa_agent",
                description="Evaluate and critique content quality",
                input_schema={
                    "content": InputField(
                        key="content",
                        label="Content to Assess",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        description="Content to evaluate",
                    ),
                    "criteria": InputField(
                        key="criteria",
                        label="Assessment Criteria",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        placeholder="Quality criteria to evaluate against",
                        description="Specific criteria for assessment",
                    ),
                    "quality_threshold": InputField(
                        key="quality_threshold",
                        label="Quality Threshold",
                        input_type=InputType.NUMBER,
                        required=False,
                        default_value=0.7,
                        description="Minimum acceptable quality score (0-1)",
                    ),
                },
                output_schema={
                    "score": OutputField(
                        key="score",
                        label="Quality Score",
                        content_type=ContentType.NUMBER,
                        description="Quality score 0-1",
                    ),
                    "feedback": OutputField(
                        key="feedback",
                        label="Assessment Feedback",
                        content_type=ContentType.TEXT,
                        description="Detailed feedback on quality",
                    ),
                    "issues": OutputField(
                        key="issues",
                        label="Issues Found",
                        content_type=ContentType.ARRAY,
                        description="List of identified issues",
                    ),
                },
                quality_threshold=0.7,
                tags=["quality-assurance"],
            )
        )

        # Refine Phase
        self.register_phase(
            PhaseDefinition(
                name="refine",
                agent_type="creative_agent",
                description="Improve content based on feedback",
                input_schema={
                    "content": InputField(
                        key="content",
                        label="Content to Refine",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        description="Content to be refined",
                    ),
                    "feedback": InputField(
                        key="feedback",
                        label="Feedback to Address",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        description="Feedback or issues to address",
                    ),
                    "revision_instructions": InputField(
                        key="revision_instructions",
                        label="Revision Instructions",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        placeholder="How should the content be improved?",
                        description="Specific instructions for refinement",
                    ),
                },
                output_schema={
                    "refined": OutputField(
                        key="refined",
                        label="Refined Content",
                        content_type=ContentType.TEXT,
                        description="The refined and improved content",
                    ),
                },
                tags=["content-generation", "refinement"],
            )
        )

        # Image Phase
        self.register_phase(
            PhaseDefinition(
                name="image",
                agent_type="image_agent",
                description="Select or generate images for content",
                input_schema={
                    "topic": InputField(
                        key="topic",
                        label="Image Topic",
                        input_type=InputType.TEXT,
                        required=False,
                        description="Topic or subject for image",
                    ),
                    "prompt": InputField(
                        key="prompt",
                        label="Image Prompt",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        placeholder="Description of desired image",
                        description="Prompt for image generation/selection",
                    ),
                    "style": InputField(
                        key="style",
                        label="Image Style",
                        input_type=InputType.SELECT,
                        required=False,
                        options=[
                            {"label": "Photorealistic", "value": "photorealistic"},
                            {"label": "Illustration", "value": "illustration"},
                            {"label": "Diagram", "value": "diagram"},
                            {"label": "Abstract", "value": "abstract"},
                        ],
                        description="Style preference for images",
                    ),
                },
                output_schema={
                    "image_url": OutputField(
                        key="image_url",
                        label="Image URL",
                        content_type=ContentType.TEXT,
                        description="URL or path to selected/generated image",
                    ),
                    "alt_text": OutputField(
                        key="alt_text",
                        label="Alt Text",
                        content_type=ContentType.TEXT,
                        description="Accessibility alt text for image",
                    ),
                    "metadata": OutputField(
                        key="metadata",
                        label="Image Metadata",
                        content_type=ContentType.OBJECT,
                        description="Image metadata (source, license, etc.)",
                    ),
                },
                tags=["media"],
            )
        )

        # Publish Phase
        self.register_phase(
            PhaseDefinition(
                name="publish",
                agent_type="publishing_agent",
                description="Format and publish content to CMS",
                input_schema={
                    "content": InputField(
                        key="content",
                        label="Content to Publish",
                        input_type=InputType.TEXTAREA,
                        required=False,
                        description="Final content for publishing",
                    ),
                    "title": InputField(
                        key="title",
                        label="Content Title",
                        input_type=InputType.TEXT,
                        required=False,
                        description="Title of the content",
                    ),
                    "target": InputField(
                        key="target",
                        label="Publish Target",
                        input_type=InputType.SELECT,
                        required=False,
                        default_value="blog",
                        options=[
                            {"label": "Blog", "value": "blog"},
                            {"label": "Newsletter", "value": "newsletter"},
                            {"label": "Social Media", "value": "social"},
                        ],
                        description="Where to publish the content",
                    ),
                    "slug": InputField(
                        key="slug",
                        label="URL Slug",
                        input_type=InputType.TEXT,
                        required=False,
                        description="URL-friendly identifier",
                    ),
                    "tags": InputField(
                        key="tags",
                        label="Tags",
                        input_type=InputType.TEXT,
                        required=False,
                        placeholder="Comma-separated tags",
                        description="Content tags for categorization",
                    ),
                },
                output_schema={
                    "published_url": OutputField(
                        key="published_url",
                        label="Published URL",
                        content_type=ContentType.TEXT,
                        description="URL where content was published",
                    ),
                    "metadata": OutputField(
                        key="metadata",
                        label="Publication Metadata",
                        content_type=ContentType.OBJECT,
                        description="Publication details and metadata",
                    ),
                },
                tags=["publishing"],
            )
        )

        logger.info(f"Initialized PhaseRegistry with {len(self._phases)} built-in phases")
