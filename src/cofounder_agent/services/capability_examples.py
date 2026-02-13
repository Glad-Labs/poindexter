"""
Capability Examples - Concrete implementations showing how to create composable capabilities.

These examples demonstrate:
1. Function-based capabilities (simplest)
2. Class-based capabilities (with state)
3. Agent method wrapping (from existing agents)
4. Async/sync capabilities

Capabilities are automatically discovered and registered via the introspection layer.
"""

from typing import Any, Dict, Optional
import asyncio

from services.capability_registry import (
    Capability,
    CapabilityMetadata,
    InputSchema,
    OutputSchema,
    ParameterSchema,
    ParameterType,
    get_registry,
)


# ============================================================================
# FUNCTION-BASED CAPABILITIES (Simplest)
# ============================================================================

async def research_capability(topic: str, depth: str = "medium") -> Dict[str, Any]:
    """
    Research a topic and gather information.
    
    Args:
        topic (string): Topic to research
        depth (string): Research depth - shallow, medium, deep
    
    Returns:
        Research findings and sources
    """
    # In real implementation, would call research_agent.research()
    await asyncio.sleep(0.1)  # Simulate work
    
    return {
        "topic": topic,
        "findings": f"Research data for {topic}",
        "sources": ["source1", "source2"],
        "depth": depth,
    }


async def generate_content_capability(
    topic: str,
    style: str = "professional",
    length: str = "medium"
) -> Dict[str, Any]:
    """
    Generate content on a topic with specified style.
    
    Args:
        topic (string): Content topic
        style (string): Writing style - casual, professional, academic
        length (string): Content length - short, medium, long
    
    Returns:
        Generated content and metadata
    """
    await asyncio.sleep(0.1)
    
    return {
        "topic": topic,
        "content": f"Generated {length} {style} content about {topic}",
        "word_count": 500 if length == "medium" else 1000,
        "style": style,
    }


async def critique_capability(content: str, focus: str = "quality") -> Dict[str, Any]:
    """
    Critique content and provide feedback.
    
    Args:
        content (string): Content to critique
        focus (string): Critique focus - quality, clarity, accuracy
    
    Returns:
        Feedback and suggestions
    """
    await asyncio.sleep(0.1)
    
    return {
        "feedback": f"Critique of content (focus: {focus})",
        "score": 8.5,
        "suggestions": ["Suggestion 1", "Suggestion 2"],
        "focus": focus,
    }


async def select_images_capability(topic: str, count: int = 3) -> Dict[str, Any]:
    """
    Select images for a topic.
    
    Args:
        topic (string): Topic for image selection
        count (integer): Number of images to select
    
    Returns:
        Selected images with metadata
    """
    await asyncio.sleep(0.1)
    
    return {
        "topic": topic,
        "images": [
            {
                "url": f"https://unsplash.com/search/{topic}_{i}",
                "title": f"Image {i+1}",
                "alt_text": f"Image of {topic}"
            }
            for i in range(count)
        ],
        "count": count,
    }


async def publish_capability(
    content: str,
    platform: str = "blog",
    schedule_at: Optional[str] = None
) -> Dict[str, Any]:
    """
    Publish content to a platform.
    
    Args:
        content (string): Content to publish
        platform (string): Platform - blog, twitter, linkedin
        schedule_at (string): Optional scheduled publish time (ISO format)
    
    Returns:
        Publication result
    """
    await asyncio.sleep(0.1)
    
    return {
        "platform": platform,
        "status": "published",
        "url": f"https://{platform}.example.com/post/12345",
        "published_at": schedule_at or "now",
    }


# ============================================================================
# CLASS-BASED CAPABILITIES (With State/Dependencies)
# ============================================================================

class FinancialAnalysisCapability(Capability):
    """
    Example class-based capability for financial analysis.
    
    Shows how to create stateful capabilities with dependencies.
    """
    
    @property
    def metadata(self) -> CapabilityMetadata:
        """Return capability metadata."""
        return CapabilityMetadata(
            name="financial.analysis",
            description="Analyze financial data and provide insights",
            tags=["financial", "analysis"],
            version="1.0.0",
            cost_tier="balanced",
        )
    
    @property
    def input_schema(self) -> InputSchema:
        """Return input schema."""
        return InputSchema(parameters=[
            ParameterSchema(
                name="company_id",
                type=ParameterType.STRING,
                description="Company identifier",
                required=True,
            ),
            ParameterSchema(
                name="period",
                type=ParameterType.STRING,
                description="Analysis period (quarterly, annual)",
                required=False,
                default="quarterly",
            ),
        ])
    
    @property
    def output_schema(self) -> OutputSchema:
        """Return output schema."""
        return OutputSchema(
            return_type=ParameterType.OBJECT,
            description="Financial analysis results",
        )
    
    async def execute(self, **inputs) -> Dict[str, Any]:
        """Execute the financial analysis."""
        company_id = inputs.get("company_id")
        period = inputs.get("period", "quarterly")
        
        # In real implementation, would call financial_agent methods
        await asyncio.sleep(0.1)
        
        return {
            "company_id": company_id,
            "period": period,
            "revenue": 1000000,
            "profit": 250000,
            "roi": "25%",
        }


class ComplianceCheckCapability(Capability):
    """Example capability for compliance checking."""
    
    @property
    def metadata(self) -> CapabilityMetadata:
        """Return capability metadata."""
        return CapabilityMetadata(
            name="compliance.check",
            description="Check content for compliance with regulations",
            tags=["compliance", "legal"],
            version="1.0.0",
            cost_tier="balanced",
        )
    
    @property
    def input_schema(self) -> InputSchema:
        """Return input schema."""
        return InputSchema(parameters=[
            ParameterSchema(
                name="content",
                type=ParameterType.STRING,
                description="Content to check",
                required=True,
            ),
            ParameterSchema(
                name="regulations",
                type=ParameterType.ARRAY,
                description="Regulations to check against",
                required=False,
            ),
        ])
    
    @property
    def output_schema(self) -> OutputSchema:
        """Return output schema."""
        return OutputSchema(
            return_type=ParameterType.OBJECT,
            description="Compliance check results",
        )
    
    async def execute(self, **inputs) -> Dict[str, Any]:
        """Execute compliance check."""
        content = inputs.get("content", "")
        regulations = inputs.get("regulations", [])
        
        await asyncio.sleep(0.1)
        
        return {
            "is_compliant": True,
            "issues_found": [],
            "confidence": 0.95,
            "regulations_checked": regulations,
        }


# ============================================================================
# AUTO-REGISTRATION ENTRY POINT
# ============================================================================

def register_example_capabilities():
    """
    Register all example capabilities with the global registry.
    
    Call this during application startup to make these capabilities available.
    In production, this would scan agent packages for capabilities automatically.
    """
    registry = get_registry()
    
    # Register function-based capabilities
    registry.register_function(
        func=research_capability,
        name="research",
        description="Research a topic and gather information",
        input_schema=InputSchema(parameters=[
            ParameterSchema(
                name="topic",
                type=ParameterType.STRING,
                description="Topic to research",
                required=True,
            ),
            ParameterSchema(
                name="depth",
                type=ParameterType.STRING,
                description="Research depth",
                required=False,
                default="medium",
                enum_values=["shallow", "medium", "deep"],
            ),
        ]),
        output_schema=OutputSchema(description="Research findings"),
        tags=["research", "information"],
        cost_tier="balanced",
    )
    
    registry.register_function(
        func=generate_content_capability,
        name="generate_content",
        description="Generate content with specified style",
        input_schema=InputSchema(parameters=[
            ParameterSchema(
                name="topic",
                type=ParameterType.STRING,
                description="Content topic",
                required=True,
            ),
            ParameterSchema(
                name="style",
                type=ParameterType.STRING,
                description="Writing style",
                required=False,
                default="professional",
                enum_values=["casual", "professional", "academic"],
            ),
            ParameterSchema(
                name="length",
                type=ParameterType.STRING,
                description="Content length",
                required=False,
                default="medium",
                enum_values=["short", "medium", "long"],
            ),
        ]),
        output_schema=OutputSchema(description="Generated content"),
        tags=["content", "generation"],
        cost_tier="balanced",
    )
    
    registry.register_function(
        func=critique_capability,
        name="critique",
        description="Critique content and provide feedback",
        input_schema=InputSchema(parameters=[
            ParameterSchema(
                name="content",
                type=ParameterType.STRING,
                description="Content to critique",
                required=True,
            ),
            ParameterSchema(
                name="focus",
                type=ParameterType.STRING,
                description="Critique focus",
                required=False,
                default="quality",
                enum_values=["quality", "clarity", "accuracy"],
            ),
        ]),
        output_schema=OutputSchema(description="Critique feedback"),
        tags=["qa", "critique"],
        cost_tier="cheap",
    )
    
    registry.register_function(
        func=select_images_capability,
        name="select_images",
        description="Select images for a topic",
        input_schema=InputSchema(parameters=[
            ParameterSchema(
                name="topic",
                type=ParameterType.STRING,
                description="Topic for image selection",
                required=True,
            ),
            ParameterSchema(
                name="count",
                type=ParameterType.INTEGER,
                description="Number of images",
                required=False,
                default=3,
            ),
        ]),
        output_schema=OutputSchema(description="Selected images"),
        tags=["images", "media"],
        cost_tier="cheap",
    )
    
    registry.register_function(
        func=publish_capability,
        name="publish",
        description="Publish content to a platform",
        input_schema=InputSchema(parameters=[
            ParameterSchema(
                name="content",
                type=ParameterType.STRING,
                description="Content to publish",
                required=True,
            ),
            ParameterSchema(
                name="platform",
                type=ParameterType.STRING,
                description="Publishing platform",
                required=False,
                default="blog",
                enum_values=["blog", "twitter", "linkedin"],
            ),
            ParameterSchema(
                name="schedule_at",
                type=ParameterType.STRING,
                description="Scheduled publish time (ISO format)",
                required=False,
            ),
        ]),
        output_schema=OutputSchema(description="Publication result"),
        tags=["publishing"],
        cost_tier="cheap",
    )
    
    # Register class-based capabilities
    registry.register(FinancialAnalysisCapability())
    registry.register(ComplianceCheckCapability())
    
    print(f"✅ Registered {len(registry.list_capabilities())} example capabilities")
