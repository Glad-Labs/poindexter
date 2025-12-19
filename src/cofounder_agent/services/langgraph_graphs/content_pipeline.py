"""LangGraph workflow for blog post creation"""

import logging
import asyncio
from typing import Literal, Optional
from datetime import datetime
import time
from langgraph.graph import StateGraph, END
from .states import ContentPipelineState
from services.model_selector_service import ModelSelector, QualityPreference

logger = logging.getLogger(__name__)

# ============================================================================
# INITIALIZATION
# ============================================================================

# Initialize model selector for cost tracking
model_selector = ModelSelector()

# ============================================================================
# NODE FUNCTIONS
# ============================================================================

async def research_phase(
    state: ContentPipelineState,
    llm_service,
    db_service=None
) -> ContentPipelineState:
    """Research: Gather information about the topic"""
    
    logger.info(f"Starting research for topic: {state['topic']}")
    
    # Determine which model to use for this phase
    phase = "research"
    if state.get('models_by_phase') and phase in state['models_by_phase']:
        model = state['models_by_phase'][phase]
    else:
        # Auto-select based on quality preference
        quality = state.get('quality_preference', 'balanced')
        quality_enum = QualityPreference[quality.upper()] if quality in ['fast', 'balanced', 'quality'] else QualityPreference.BALANCED
        model = model_selector.auto_select(phase, quality_enum)
    
    # Estimate cost for this phase
    cost = model_selector.estimate_cost(phase, model)
    logger.info(f"Research phase using {model}: Estimated cost ${cost:.6f}")
    
    prompt = f"""Research the following topic and provide key insights:

Topic: {state['topic']}
Audience: {state['audience']}
Keywords to include: {', '.join(state['keywords'])}
Tone: {state['tone']}

Please provide:
1. Key facts and data points
2. Recent developments
3. Expert opinions
4. Relevant statistics
"""
    
    start_time = time.time()
    try:
        # Simulate LLM call (in production, use actual LLM service)
        research = await llm_service.generate(prompt) if llm_service else f"Research data for {state['topic']}"
        
        state["research_notes"] = research
        state["messages"].append({
            "role": "system",
            "content": f"Research completed: {len(research)} characters gathered using {model}"
        })
        
        # Log cost to database if service available
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": True
            })
        
        # Track cost in state
        if "cost_breakdown" not in state:
            state["cost_breakdown"] = {}
        state["cost_breakdown"][phase] = cost
        state["total_cost"] = state.get("total_cost", 0.0) + cost
        
    except Exception as e:
        logger.error(f"Research phase error: {str(e)}")
        state["errors"].append(f"Research failed: {str(e)}")
        state["status"] = "failed"
        
        # Log failed cost attempt
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": False,
                "error_message": str(e)
            })
    
    return state


async def outline_phase(
    state: ContentPipelineState,
    llm_service,
    db_service=None
) -> ContentPipelineState:
    """Outline: Create structure for the blog post"""
    
    logger.info(f"Creating outline for: {state['topic']}")
    
    # Determine which model to use for this phase
    phase = "outline"
    if state.get('models_by_phase') and phase in state['models_by_phase']:
        model = state['models_by_phase'][phase]
    else:
        quality = state.get('quality_preference', 'balanced')
        quality_enum = QualityPreference[quality.upper()] if quality in ['fast', 'balanced', 'quality'] else QualityPreference.BALANCED
        model = model_selector.auto_select(phase, quality_enum)
    
    cost = model_selector.estimate_cost(phase, model)
    logger.info(f"Outline phase using {model}: Estimated cost ${cost:.6f}")
    
    prompt = f"""Create a detailed outline for a blog post with these specifications:

Title: {state['topic']}
Target audience: {state['audience']}
Tone: {state['tone']}
Target word count: {state['word_count']}

Research gathered:
{state['research_notes'][:2000] if state['research_notes'] else 'No research available'}...

Create an outline with:
- Title
- Introduction (hook)
- Main sections (3-5 with subsections)
- Conclusion
- Call-to-action

Format as a numbered list.
"""
    
    start_time = time.time()
    try:
        outline = await llm_service.generate(prompt) if llm_service else f"Outline for {state['topic']}"
        state["outline"] = outline
        state["messages"].append({
            "role": "system",
            "content": f"Outline created successfully using {model}"
        })
        
        # Log cost
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": True
            })
        
        # Track in state
        if "cost_breakdown" not in state:
            state["cost_breakdown"] = {}
        state["cost_breakdown"][phase] = cost
        state["total_cost"] = state.get("total_cost", 0.0) + cost
        
    except Exception as e:
        logger.error(f"Outline phase error: {str(e)}")
        state["errors"].append(f"Outline failed: {str(e)}")
        state["status"] = "failed"
        
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": False,
                "error_message": str(e)
            })
    
    return state


async def draft_phase(
    state: ContentPipelineState,
    llm_service,
    db_service=None
) -> ContentPipelineState:
    """Draft: Write the full blog post"""
    
    logger.info(f"Drafting blog post: {state['topic']}")
    
    # Determine which model to use for this phase
    phase = "draft"
    if state.get('models_by_phase') and phase in state['models_by_phase']:
        model = state['models_by_phase'][phase]
    else:
        quality = state.get('quality_preference', 'balanced')
        quality_enum = QualityPreference[quality.upper()] if quality in ['fast', 'balanced', 'quality'] else QualityPreference.BALANCED
        model = model_selector.auto_select(phase, quality_enum)
    
    cost = model_selector.estimate_cost(phase, model)
    logger.info(f"Draft phase using {model}: Estimated cost ${cost:.6f}")
    
    prompt = f"""Write a comprehensive blog post based on this outline and research:

OUTLINE:
{state['outline'][:2000] if state['outline'] else 'No outline available'}

RESEARCH NOTES:
{state['research_notes'][:2000] if state['research_notes'] else 'No research available'}

SPECIFICATIONS:
- Tone: {state['tone']}
- Target word count: {state['word_count']}
- Keywords to naturally incorporate: {', '.join(state['keywords'])}
- Audience: {state['audience']}

Write the complete blog post with proper formatting:
- Use markdown headers for sections
- Include introduction, main content, and conclusion
- Incorporate research naturally
- Aim for {state['word_count']} words
- Make it engaging and informative
"""
    
    start_time = time.time()
    try:
        draft = await llm_service.generate(prompt) if llm_service else f"Draft content for {state['topic']}"
        state["draft"] = draft
        state["messages"].append({
            "role": "system",
            "content": f"Draft completed using {model}"
        })
        
        # Log cost
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": True
            })
        
        # Track in state
        if "cost_breakdown" not in state:
            state["cost_breakdown"] = {}
        state["cost_breakdown"][phase] = cost
        state["total_cost"] = state.get("total_cost", 0.0) + cost
        
    except Exception as e:
        logger.error(f"Draft phase error: {str(e)}")
        state["errors"].append(f"Draft failed: {str(e)}")
        state["status"] = "failed"
        
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": False,
                "error_message": str(e)
            })
    
    return state


async def assess_quality(
    state: ContentPipelineState,
    quality_service,
    db_service=None
) -> ContentPipelineState:
    """Assess: Evaluate content quality"""
    
    logger.info(f"Assessing quality for task {state['request_id']}")
    
    # Determine which model to use for this phase (quality assessment critical, force best)
    phase = "assess"
    if state.get('models_by_phase') and phase in state['models_by_phase']:
        model = state['models_by_phase'][phase]
    else:
        # Quality assessment requires best model
        model = "gpt-4"
    
    cost = model_selector.estimate_cost(phase, model)
    logger.info(f"Assessment phase using {model}: Estimated cost ${cost:.6f}")
    
    start_time = time.time()
    try:
        # Use existing quality service
        if quality_service:
            assessment_result = await quality_service.evaluate(
                content=state["draft"],
                context={
                    "topic": state["topic"],
                    "keywords": state["keywords"],
                    "tone": state["tone"]
                }
            )
            # Handle QualityAssessment object (not dict)
            assessment = {
                "score": assessment_result.overall_score if hasattr(assessment_result, 'overall_score') else assessment_result.get("score", 0),
                "passed": assessment_result.passing if hasattr(assessment_result, 'passing') else assessment_result.get("passed", False),
                "feedback": assessment_result.feedback if hasattr(assessment_result, 'feedback') else assessment_result.get("feedback", "")
            }
        else:
            assessment = {"score": 85, "passed": True, "feedback": "Quality assessment simulated"}
        
        state["quality_score"] = assessment.get("score", 0)
        state["quality_feedback"] = assessment.get("feedback", "")
        state["passed_quality"] = assessment.get("passed", False)
        
        quality_msg = f"Quality: {state['quality_score']}/100 - {'PASSED' if state['passed_quality'] else 'NEEDS REFINEMENT'}"
        state["messages"].append({
            "role": "system",
            "content": quality_msg
        })
        
        # Log cost
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "quality_score": state["quality_score"] / 20.0,  # Convert 0-100 to 0-5
                "success": True
            })
        
        # Track in state
        if "cost_breakdown" not in state:
            state["cost_breakdown"] = {}
        state["cost_breakdown"][phase] = cost
        state["total_cost"] = state.get("total_cost", 0.0) + cost
        
    except Exception as e:
        logger.error(f"Quality assessment error: {str(e)}")
        state["errors"].append(f"Quality assessment failed: {str(e)}")
        # Don't fail entirely - allow refinement
        state["quality_score"] = 50
        state["passed_quality"] = False
        
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": False,
                "error_message": str(e)
            })
    
    return state


def should_refine(state: ContentPipelineState) -> Literal["refine", "finalize"]:
    """Decision node: Should we refine or finalize?"""
    
    if state["passed_quality"]:
        logger.info(f"Quality passed for {state['request_id']}")
        return "finalize"
    
    if state["refinement_count"] >= state["max_refinements"]:
        logger.warning(f"Max refinements reached for {state['request_id']}")
        return "finalize"
    
    logger.info(f"Refining {state['request_id']} (attempt {state['refinement_count'] + 1})")
    return "refine"


async def refine_phase(
    state: ContentPipelineState,
    llm_service,
    db_service=None
) -> ContentPipelineState:
    """Refine: Improve content based on quality feedback"""
    
    logger.info(f"Refining content for {state['request_id']}")
    
    # Determine which model to use for this phase
    phase = "refine"
    if state.get('models_by_phase') and phase in state['models_by_phase']:
        model = state['models_by_phase'][phase]
    else:
        quality = state.get('quality_preference', 'balanced')
        quality_enum = QualityPreference[quality.upper()] if quality in ['fast', 'balanced', 'quality'] else QualityPreference.BALANCED
        model = model_selector.auto_select(phase, quality_enum)
    
    cost = model_selector.estimate_cost(phase, model)
    logger.info(f"Refine phase using {model}: Estimated cost ${cost:.6f}")
    
    prompt = f"""Improve this blog post based on the following feedback:

CURRENT CONTENT:
{state['draft'][:2000] if state['draft'] else 'No content available'}...

FEEDBACK FOR IMPROVEMENT:
{state['quality_feedback'] if state['quality_feedback'] else 'General improvement needed'}

Please revise the content to address all feedback while maintaining:
- Tone: {state['tone']}
- Keywords: {', '.join(state['keywords'])}
- Target audience: {state['audience']}
- Approximately {state['word_count']} words

Return the improved version.
"""
    
    start_time = time.time()
    try:
        refined = await llm_service.generate(prompt) if llm_service else state["draft"]
        state["draft"] = refined
        state["refinement_count"] += 1
        
        state["messages"].append({
            "role": "system",
            "content": f"Refinement {state['refinement_count']} complete using {model}"
        })
        
        # Log cost
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": True
            })
        
        # Track in state
        if "cost_breakdown" not in state:
            state["cost_breakdown"] = {}
        state["cost_breakdown"][phase] = state.get("cost_breakdown", {}).get(phase, 0.0) + cost
        state["total_cost"] = state.get("total_cost", 0.0) + cost
        
    except Exception as e:
        logger.error(f"Refinement phase error: {str(e)}")
        state["errors"].append(f"Refinement failed: {str(e)}")
        
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": False,
                "error_message": str(e)
            })
    
    return state


async def finalize_phase(
    state: ContentPipelineState,
    metadata_service,
    db_service
) -> ContentPipelineState:
    """Finalize: Generate metadata and save"""
    
    logger.info(f"Finalizing content for {state['request_id']}")
    
    # Determine which model to use for this phase
    phase = "finalize"
    if state.get('models_by_phase') and phase in state['models_by_phase']:
        model = state['models_by_phase'][phase]
    else:
        quality = state.get('quality_preference', 'balanced')
        quality_enum = QualityPreference[quality.upper()] if quality in ['fast', 'balanced', 'quality'] else QualityPreference.BALANCED
        model = model_selector.auto_select(phase, quality_enum)
    
    cost = model_selector.estimate_cost(phase, model)
    logger.info(f"Finalize phase using {model}: Estimated cost ${cost:.6f}")
    
    start_time = time.time()
    try:
        # Generate metadata if service available
        if metadata_service:
            metadata = await metadata_service.generate(
                content=state["draft"],
                topic=state["topic"],
                keywords=state["keywords"]
            )
        else:
            metadata = {
                "title": state["topic"],
                "description": state["draft"][:160],
                "keywords": state["keywords"]
            }
        
        state["final_content"] = state["draft"]
        state["metadata"] = metadata
        state["completed_at"] = datetime.now()
        state["status"] = "completed"
        
        # Save to database if service available
        if db_service:
            # Create unique slug by appending request_id suffix (avoid constraint violations)
            base_slug = state["topic"].lower().replace(" ", "-").replace("_", "-")[:80]
            # Add first 8 chars of request_id to ensure uniqueness across multiple runs with same topic
            unique_slug = f"{base_slug}-{state['request_id'][:8]}"
            
            # Use create_post method (which exists)
            task_id = await db_service.create_post({
                "title": state["topic"],
                "content": state["draft"],
                "excerpt": state["draft"][:160] if state["draft"] else "",
                "slug": unique_slug,
                "status": "draft",
                "seo_title": metadata.get("title", state["topic"]),
                "seo_description": metadata.get("description", state["draft"][:160]),
                "seo_keywords": ",".join(state["keywords"]) if state["keywords"] else "",
                "metadata": state["metadata"]
            })
            state["task_id"] = task_id.get("id") if isinstance(task_id, dict) else task_id
        else:
            state["task_id"] = state["request_id"]
        
        state["messages"].append({
            "role": "system",
            "content": f"Content saved with ID: {state['task_id']}"
        })
        
        # Log finalization cost
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": True
            })
        
        # Track in state (final cost entry)
        if "cost_breakdown" not in state:
            state["cost_breakdown"] = {}
        state["cost_breakdown"][phase] = cost
        state["total_cost"] = state.get("total_cost", 0.0) + cost
        
        logger.info(f"✅ Task {state['request_id']} completed. Total cost: ${state['total_cost']:.6f}")
        
    except Exception as e:
        logger.error(f"Finalize phase error: {str(e)}")
        state["errors"].append(f"Finalization failed: {str(e)}")
        state["task_id"] = state["request_id"]
        
        duration_ms = int((time.time() - start_time) * 1000)
        if db_service:
            await db_service.log_cost({
                "task_id": state["request_id"],
                "user_id": state.get("user_id"),
                "phase": phase,
                "model": model,
                "provider": "ollama" if model == "ollama" else "openai" if "gpt" in model else "anthropic",
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "success": False,
                "error_message": str(e)
            })
    
    return state


# ============================================================================
# GRAPH CONSTRUCTION
# ============================================================================

def create_content_pipeline_graph(
    llm_service=None,
    quality_service=None,
    metadata_service=None,
    db_service=None
):
    """Build the content creation workflow graph"""
    
    workflow = StateGraph(ContentPipelineState)
    
    # Create partial functions with services bound (now includes db_service for cost logging)
    async def research(state):
        return await research_phase(state, llm_service, db_service)
    
    async def outline(state):
        return await outline_phase(state, llm_service, db_service)
    
    async def draft(state):
        return await draft_phase(state, llm_service, db_service)
    
    async def assess(state):
        return await assess_quality(state, quality_service, db_service)
    
    async def refine(state):
        return await refine_phase(state, llm_service, db_service)
    
    async def finalize(state):
        return await finalize_phase(state, metadata_service, db_service)
    
    # Add nodes
    workflow.add_node("research", research)
    workflow.add_node("outline", outline)
    workflow.add_node("draft", draft)
    workflow.add_node("assess", assess)
    workflow.add_node("refine", refine)
    workflow.add_node("finalize", finalize)
    
    # Add edges (linear flow)
    workflow.add_edge("research", "outline")
    workflow.add_edge("outline", "draft")
    workflow.add_edge("draft", "assess")
    
    # Add conditional edges (refinement loop)
    workflow.add_conditional_edges(
        "assess",
        should_refine,
        {
            "refine": "refine",      # Loop back to refine
            "finalize": "finalize"   # Go to finalize
        }
    )
    
    # Loop: refine -> assess
    workflow.add_edge("refine", "assess")
    
    # End
    workflow.add_edge("finalize", END)
    
    # Set entry point
    workflow.set_entry_point("research")
    
    # Compile
    return workflow.compile()
