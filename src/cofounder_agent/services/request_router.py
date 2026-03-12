"""
Request Router — Natural-language intent classifier for the unified orchestrator.

Responsibility: map a free-form user text string onto one of the well-known
RequestType values and return a fully-populated Request object.

The router uses deterministic keyword matching (fast, zero LLM cost) and can be
extended with an LLM-based fallback without touching UnifiedOrchestrator.

Public surface
--------------
classify_request(user_input, request_id, context, extract_content_params_fn) -> Request

Internal helpers
----------------
_ROUTING_TABLE  — ordered list of (keywords, RequestType, intent) tuples
_match_routing_table(text) -> (RequestType, intent) | None
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from services.logger_config import get_logger

from .orchestrator_types import Request, RequestType

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Routing table — evaluated top-to-bottom; first match wins.
# Each entry: (keywords: List[str], request_type: RequestType, intent: str)
# ---------------------------------------------------------------------------
_ROUTING_TABLE: List[Tuple[List[str], RequestType, str]] = [
    # Content creation — high specificity phrases first
    (
        ["create content", "write about", "new post", "blog post"],
        RequestType.CONTENT_CREATION,
        "content_creation",
    ),
    # Content sub-tasks
    (
        ["research", "research about", "find info"],
        RequestType.CONTENT_SUBTASK,
        "research",
    ),
    (
        ["creative", "draft", "generate text"],
        RequestType.CONTENT_SUBTASK,
        "creative",
    ),
    # Financial
    (
        ["financial", "budget", "spending", "revenue", "balance"],
        RequestType.FINANCIAL_ANALYSIS,
        "financial_analysis",
    ),
    # Compliance
    (
        ["compliance", "audit", "security", "risk"],
        RequestType.COMPLIANCE_CHECK,
        "compliance_check",
    ),
    # Task management
    (
        ["create task", "new task", "add task"],
        RequestType.TASK_MANAGEMENT,
        "create_task",
    ),
    # Information retrieval — broad short keywords, lower priority
    (
        ["what ", "show me ", "tell me ", "list ", "get "],
        RequestType.INFORMATION_RETRIEVAL,
        "retrieve_info",
    ),
    # Decision support
    (
        ["should i ", "should we ", "what should ", "recommend", "suggest"],
        RequestType.DECISION_SUPPORT,
        "decision_support",
    ),
    # System operations
    (
        ["help", "status", "health", "commands", "what can you do"],
        RequestType.SYSTEM_OPERATION,
        "system_info",
    ),
    # Intervention / override
    (
        ["stop", "cancel", "intervene", "emergency", "override"],
        RequestType.INTERVENTION,
        "intervention",
    ),
]


def _match_routing_table(text_lower: str) -> Optional[Tuple[RequestType, str]]:
    """
    Scan the routing table and return the first (RequestType, intent) match.

    Returns None when no rule fires (caller should use the default route).
    """
    for keywords, request_type, intent in _ROUTING_TABLE:
        if any(kw in text_lower for kw in keywords):
            return request_type, intent
    return None


def classify_request(
    user_input: str,
    request_id: str,
    context: Optional[Dict[str, Any]],
    extract_content_params_fn: Callable[[str], Dict[str, Any]],
) -> Request:
    """
    Parse a natural-language user input string and return a classified Request.

    Args:
        user_input: Raw text from the caller.
        request_id: UUID string for this request (caller-generated).
        context: Optional execution context dict.
        extract_content_params_fn: Callable that extracts content parameters
            from ``user_input``; called only for CONTENT_CREATION routes.
            Passed in as a dependency to avoid importing from UnifiedOrchestrator.

    Returns:
        A populated :class:`Request` instance ready for routing.
    """
    input_lower = user_input.lower().strip()
    ctx = context or {}

    match = _match_routing_table(input_lower)

    if match is not None:
        request_type, intent = match

        # Special-case: content subtask needs a subtask_type parameter
        if request_type == RequestType.CONTENT_SUBTASK:
            subtask_type = "research" if "research" in input_lower else "creative"
            parameters: Dict[str, Any] = {
                "subtask_type": subtask_type,
                "topic": user_input,
            }
        # Task management needs task_description
        elif request_type == RequestType.TASK_MANAGEMENT:
            parameters = {"task_description": user_input}
        # Information retrieval needs query
        elif request_type == RequestType.INFORMATION_RETRIEVAL:
            parameters = {"query": user_input}
        # Decision support needs decision_question
        elif request_type == RequestType.DECISION_SUPPORT:
            parameters = {"decision_question": user_input}
        # Content creation needs extracted params
        elif request_type == RequestType.CONTENT_CREATION:
            parameters = extract_content_params_fn(user_input)
        else:
            parameters = {}

        logger.debug(
            "[classify_request] request_id=%s matched route %s (intent=%s)",
            request_id,
            request_type.value,
            intent,
        )
        return Request(
            request_id=request_id,
            original_text=user_input,
            request_type=request_type,
            extracted_intent=intent,
            parameters=parameters,
            context=ctx,
        )

    # Default: treat as content creation
    logger.debug(
        "[classify_request] request_id=%s no rule matched — defaulting to CONTENT_CREATION",
        request_id,
    )
    return Request(
        request_id=request_id,
        original_text=user_input,
        request_type=RequestType.CONTENT_CREATION,
        extracted_intent="content_creation_default",
        parameters=extract_content_params_fn(user_input),
        context=ctx,
    )
