"""NLP Intent Recognition - Parse natural language requests to workflows.

Recognizes user intent from natural language and maps to appropriate
workflow configurations. Phase 3 component for advanced user interaction.
"""

import logging
import re
from typing import Any, Dict, Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IntentMatch:
    """Result of intent recognition."""

    intent_type: str
    confidence: float
    workflow_type: str
    parameters: Dict[str, Any]
    raw_message: str


class NLPIntentRecognizer:
    """Recognize user intent from natural language messages.

    Supports:
    - Multiple intent patterns (content, social, financial, market, etc.)
    - Parameter extraction from free text
    - Confidence scoring
    - Context-aware disambiguation
    - Pattern-based and ML-ready architecture
    """

    # Intent patterns with keywords, workflow mapping, and parameter extractors
    INTENT_PATTERNS = {
        # Content generation intents
        "content_generation": {
            "keywords": [
                r"(write|generate|create|compose|draft|author)\s+(a\s+)?(blog|article|post|content|essay|story)",
                r"(blog|article|content)\s+(about|on|regarding)\s+(.+)",
                r"generate\s+(professional\s+)?(content|text|writing)",
                r"write\s+(something\s+)?(about|on|regarding)\s+(.+)",
            ],
            "confidence_boost": 0.95,
            "parameter_extractors": [
                "extract_topic",
                "extract_style",
                "extract_length",
            ],
        },
        # Social media intents
        "social_media": {
            "keywords": [
                r"(create|write|generate)\s+(social\s+)?(media\s+)?(post|tweet|content)",
                r"post\s+(to\s+)?(twitter|linkedin|instagram|facebook|social)",
                r"(social\s+)?(media\s+)?(campaign|strategy|post)",
                r"share\s+(on\s+)?(social\s+)?media",
            ],
            "confidence_boost": 0.90,
            "parameter_extractors": [
                "extract_topic",
                "extract_platforms",
                "extract_tone",
            ],
        },
        # Financial analysis intents
        "financial_analysis": {
            "keywords": [
                r"(analyze|analyze|check|review)\s+(cost|budget|expenses|roi|revenue)",
                r"(cost|budget|financial)\s+(analysis|report|breakdown)",
                r"(what|how much)\s+(does|will|can)\s+(it\s+)?(cost|spend|budget)",
                r"(financial|budget)\s+(report|forecast|projection)",
            ],
            "confidence_boost": 0.85,
            "parameter_extractors": [
                "extract_period",
                "extract_metric_type",
            ],
        },
        # Market analysis intents
        "market_analysis": {
            "keywords": [
                r"(analyze|research|study)\s+(market|competitor|industry|trends?)",
                r"(market|competitive|industry)\s+(analysis|research|report)",
                r"(what\s+)?trends?\s+(in|for|about)\s+(.+)",
                r"(compare|analyze)\s+(competitor|competition)",
            ],
            "confidence_boost": 0.85,
            "parameter_extractors": [
                "extract_market",
                "extract_include_competitors",
            ],
        },
        # Compliance check intents
        "compliance_check": {
            "keywords": [
                r"(check|verify|validate)\s+(compliance|legal|gdpr|privacy)",
                r"(is\s+)?this\s+(content|text|post)\s+(compliant|legal|safe)",
                r"(compliance|legal|privacy)\s+(check|review|analysis)",
            ],
            "confidence_boost": 0.80,
            "parameter_extractors": [
                "extract_content_to_check",
            ],
        },
        # Performance review intents
        "performance_review": {
            "keywords": [
                r"(review|analyze|check)\s+(performance|metrics|results|kpi)",
                r"(how\s+(is|did)|performance)\s+(the\s+)?(\w+\s+)?(perform|do)",
                r"(performance|results)\s+(report|analysis|review)",
            ],
            "confidence_boost": 0.80,
            "parameter_extractors": [
                "extract_date_range",
                "extract_metrics",
            ],
        },
    }

    def __init__(self):
        """Initialize intent recognizer."""
        self.patterns = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile regex patterns for faster matching."""
        for intent_type, config in self.INTENT_PATTERNS.items():
            self.patterns[intent_type] = {
                "keywords": [re.compile(kw, re.IGNORECASE) for kw in config["keywords"]],
                "confidence_boost": config["confidence_boost"],
                "extractors": config["parameter_extractors"],
            }

    async def recognize_intent(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[IntentMatch]:
        """Recognize user intent from message.

        Args:
            message: User's natural language message
            context: Optional conversation context

        Returns:
            IntentMatch with recognized intent and parameters, or None if no match
        """
        if not message or not isinstance(message, str):
            return None

        message_lower = message.lower().strip()

        best_match = None
        best_confidence = 0.0

        # Check each intent type
        for intent_type, pattern_config in self.patterns.items():
            # Check if any keyword pattern matches
            for keyword_pattern in pattern_config["keywords"]:
                if keyword_pattern.search(message_lower):
                    # Found a match
                    base_confidence = pattern_config["confidence_boost"]

                    # Extract parameters
                    parameters = await self._extract_parameters(
                        intent_type,
                        message,
                        context or {},
                        pattern_config["extractors"],
                    )

                    # Create match
                    match = IntentMatch(
                        intent_type=intent_type,
                        confidence=base_confidence,
                        workflow_type=intent_type,
                        parameters=parameters,
                        raw_message=message,
                    )

                    # Keep best match
                    if base_confidence > best_confidence:
                        best_confidence = base_confidence
                        best_match = match

                    break  # Move to next intent type

        return best_match

    async def recognize_multiple_intents(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        top_n: int = 3,
    ) -> List[IntentMatch]:
        """Recognize multiple possible intents ranked by confidence.

        Args:
            message: User's message
            context: Optional context
            top_n: Number of top matches to return

        Returns:
            List of IntentMatch objects ranked by confidence
        """
        matches = []

        for intent_type, pattern_config in self.patterns.items():
            for keyword_pattern in pattern_config["keywords"]:
                if keyword_pattern.search(message.lower()):
                    parameters = await self._extract_parameters(
                        intent_type,
                        message,
                        context or {},
                        pattern_config["extractors"],
                    )

                    matches.append(
                        IntentMatch(
                            intent_type=intent_type,
                            confidence=pattern_config["confidence_boost"],
                            workflow_type=intent_type,
                            parameters=parameters,
                            raw_message=message,
                        )
                    )
                    break

        # Sort by confidence descending
        matches.sort(key=lambda m: m.confidence, reverse=True)

        return matches[:top_n]

    async def _extract_parameters(
        self,
        intent_type: str,
        message: str,
        context: Dict[str, Any],
        extractors: List[str],
    ) -> Dict[str, Any]:
        """Extract parameters for recognized intent.

        Args:
            intent_type: Type of intent
            message: Original message
            context: Conversation context
            extractors: List of parameter extractors to apply

        Returns:
            Dictionary of extracted parameters
        """
        parameters = {}

        for extractor_name in extractors:
            extractor = getattr(self, extractor_name, None)
            if extractor:
                extracted = await extractor(message, context)
                parameters.update(extracted)

        return parameters

    # Parameter extractors
    async def extract_topic(
        self, message: str, context: Dict[str, Any]
    ) -> Dict[str, Optional[str]]:
        """Extract topic/subject from message.

        Example: "Write about AI trends" → {"topic": "AI trends"}
        """
        # Look for "about", "on", "regarding", etc.
        patterns = [
            r"(?:about|on|regarding|on\s+the\s+topic\s+of)\s+([^.!?]+)",
            r"^(?:write|generate|create)\s+(?:a\s+)?(?:blog|article|post)\s+(?:about|on)\s+([^.!?]+)",
            r"(?:concerning|with\s+respect\s+to|relating\s+to)\s+([^.!?]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                topic = match.group(1).strip()
                return {"topic": topic}

        return {"topic": None}

    async def extract_style(self, message: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Extract writing style preference.

        Example: "Write professional content" → {"style": "professional"}
        """
        styles = [
            "professional",
            "casual",
            "academic",
            "creative",
            "formal",
            "informal",
            "technical",
            "conversational",
        ]

        message_lower = message.lower()
        for style in styles:
            if style in message_lower:
                return {"style": style}

        return {"style": "professional"}  # Default

    async def extract_length(self, message: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Extract desired content length.

        Example: "Write a 2000-word article" → {"length": "2000 words"}
        """
        # Look for word counts
        word_count_pattern = r"(\d+)[-\s]?(word|words)"
        match = re.search(word_count_pattern, message, re.IGNORECASE)
        if match:
            count = match.group(1)
            return {"length": f"{count} words"}

        # Look for length descriptors
        if any(w in message.lower() for w in ["short", "brief", "quick"]):
            return {"length": "500 words"}
        if any(w in message.lower() for w in ["long", "detailed", "comprehensive"]):
            return {"length": "3000 words"}

        return {"length": "2000 words"}  # Default

    async def extract_platforms(self, message: str, context: Dict[str, Any]) -> Dict[str, list]:
        """Extract social media platforms.

        Example: "Post to Twitter and LinkedIn" → {"platforms": ["twitter", "linkedin"]}
        """
        platforms = [
            "twitter",
            "linkedin",
            "instagram",
            "facebook",
            "tiktok",
            "youtube",
            "reddit",
            "medium",
        ]

        found_platforms = []
        message_lower = message.lower()
        for platform in platforms:
            if platform in message_lower:
                found_platforms.append(platform)

        return {
            "platforms": found_platforms or ["twitter", "linkedin"]
        }  # Default to Twitter and LinkedIn

    async def extract_tone(self, message: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Extract desired tone for social media.

        Example: "Write funny posts" → {"tone": "funny"}
        """
        tones = [
            "professional",
            "casual",
            "funny",
            "serious",
            "inspiring",
            "educational",
            "entertaining",
        ]

        message_lower = message.lower()
        for tone in tones:
            if tone in message_lower:
                return {"tone": tone}

        return {"tone": "professional"}  # Default

    async def extract_period(self, message: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Extract time period for analysis.

        Example: "Q1 2024 budget" → {"period": "Q1 2024"}
        """
        period_patterns = [
            r"(q[1-4]\s+\d{4})",
            r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}",
            r"(\d{4})",
        ]

        message_lower = message.lower()
        for pattern in period_patterns:
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                return {"period": match.group(1)}

        return {"period": "current month"}  # Default

    async def extract_metric_type(self, message: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Extract financial metric type.

        Example: "ROI analysis" → {"metric_type": "ROI"}
        """
        metrics = ["cost", "budget", "revenue", "roi", "profit", "expense"]

        message_lower = message.lower()
        for metric in metrics:
            if metric in message_lower:
                return {"metric_type": metric}

        return {"metric_type": "cost"}  # Default

    async def extract_market(self, message: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Extract market or industry.

        Example: "Market analysis for SaaS" → {"market": "SaaS"}
        """
        patterns = [
            r"(?:for|in)\s+(?:the\s+)?([a-z\s]+)\s+(?:market|industry)",
            r"(?:market|industry)\s+(?:for|in)\s+([a-z\s]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                market = match.group(1).strip()
                return {"market": market}

        return {"market": "general"}  # Default

    async def extract_include_competitors(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> Dict[str, bool]:
        """Extract whether to include competitor analysis.

        Example: "Compare with competitors" → {"include_competitors": true}
        """
        if any(w in message.lower() for w in ["competitor", "compare", "versus", "vs", "vs."]):
            return {"include_competitors": True}

        return {"include_competitors": False}

    async def extract_content_to_check(
        self, message: str, context: Dict[str, Any]
    ) -> Dict[str, str]:
        """Extract content to check for compliance.

        Example: "Check compliance of this post: ..." → {"content": "..."}
        """
        # Look for quoted content
        if "'" in message or '"' in message:
            # Extract quoted content
            patterns = [
                r'["\']([^"\']+)["\']',
                r"(?:check|verify|validate):\s*(.+)",
            ]

            for pattern in patterns:
                match = re.search(pattern, message)
                if match:
                    return {"content": match.group(1).strip()}

        return {"content": message}

    async def extract_date_range(self, message: str, context: Dict[str, Any]) -> Dict[str, str]:
        """Extract date range for performance review.

        Example: "Last 30 days performance" → {"date_range": "last_30_days"}
        """
        date_patterns = [
            (r"last\s+(\d+)\s+days", "last_{0}_days"),
            (r"(?:this|last)\s+(month|quarter|year)", "last_{0}"),
            (r"(\d+)/(\d+)/(\d+)\s+to\s+(\d+)/(\d+)/(\d+)", "custom_range"),
        ]

        for pattern, format_str in date_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return {"date_range": format_str.format(*match.groups())}

        return {"date_range": "last_30_days"}  # Default

    async def extract_metrics(self, message: str, context: Dict[str, Any]) -> Dict[str, list]:
        """Extract specific metrics to include in review.

        Example: "Show views and engagement" → {"metrics": ["views", "engagement"]}
        """
        all_metrics = [
            "views",
            "engagement",
            "shares",
            "clicks",
            "conversions",
            "roi",
            "ctr",
            "conversion_rate",
        ]

        found_metrics = []
        message_lower = message.lower()
        for metric in all_metrics:
            if metric in message_lower:
                found_metrics.append(metric)

        return {"metrics": found_metrics or ["views", "engagement"]}  # Default

    async def execute_recognized_intent(
        self,
        intent_match: IntentMatch,
        user_id: str,
        context: Optional[Dict[str, Any]] = None,
        selected_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute recognized intent via Service Layer.

        Converts IntentMatch to service action execution.
        Both manual forms and NLP inputs use this same unified backend path.

        **How it works:**
        1. NLPIntentRecognizer recognizes intent from natural language
        2. This method executes the intent via ServiceRegistry
        3. Same TaskService used by manual forms
        4. Result stored in same PostgreSQL table
        5. No code duplication - single source of truth
        6. Uses selected model for task execution if provided

        Args:
            intent_match: Recognized intent with workflow type and parameters
            user_id: User executing the intent
            context: Optional execution context
            selected_model: Optional model selection (e.g., "ollama-mistral", "openai-gpt4")

        Returns:
            Dict with:
                - success: bool (action succeeded)
                - intent_type: str (recognized intent)
                - action: str (service action name)
                - service: str (service name)
                - result: dict (action result data)
                - errors: list (any error messages)
        """
        try:
            from services.service_base import get_service_registry

            registry = get_service_registry()
            if not registry:
                logger.error("Service registry unavailable for intent execution")
                return {
                    "success": False,
                    "error": "Service registry unavailable",
                    "intent_type": intent_match.intent_type,
                }

            # Map intent to appropriate service
            service_name = self._map_intent_to_service(intent_match.intent_type)
            service = registry.get_service(service_name)

            if not service:
                logger.error(
                    f"Service '{service_name}' not found for intent '{intent_match.intent_type}'"
                )
                return {
                    "success": False,
                    "error": f"Service '{service_name}' not found",
                    "intent_type": intent_match.intent_type,
                }

            # Execute the service action
            action_name = intent_match.intent_type  # e.g., 'create_task'
            execution_context = context or {}
            execution_context["user_id"] = user_id
            execution_context["source"] = "nlp_agent"
            execution_context["raw_message"] = intent_match.raw_message

            # Include selected model in context if provided
            if selected_model:
                execution_context["selected_model"] = selected_model
                logger.info(f"Using selected model for execution: {selected_model}")

            logger.info(
                f"Executing intent: service={service_name}, action={action_name}, "
                f"confidence={intent_match.confidence}"
            )

            result = await service.execute_action(
                action_name,
                intent_match.parameters,
                execution_context,
            )

            success = (
                result.status == "success"
                if hasattr(result, "status")
                else result.get("success", False)
            )

            return {
                "success": success,
                "intent_type": intent_match.intent_type,
                "action": action_name,
                "service": service_name,
                "confidence": intent_match.confidence,
                "result": result.data if hasattr(result, "data") else result,
                "errors": result.errors if hasattr(result, "errors") else [],
            }

        except Exception as e:
            logger.error(f"Error executing recognized intent: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "intent_type": intent_match.intent_type,
            }

    def _map_intent_to_service(self, intent_type: str) -> str:
        """Map intent type to service name.

        Maps recognized intents to their corresponding services:
        - create_task, list_tasks, get_task, update_task_status → tasks
        - create_content, list_content → content
        - analyze_market, research_market → market_analysis
        - check_financial → financial_analysis

        Args:
            intent_type: Recognized intent type (e.g., 'create_task')

        Returns:
            Service name (e.g., 'tasks')
        """
        # Extract resource name from intent_type
        # Format: action_resource (e.g., create_task, analyze_market)
        parts = intent_type.split("_")

        if len(parts) < 2:
            # Single word intent - try direct mapping
            return intent_type + "s"

        # Get resource part (everything after the action)
        resource = "_".join(parts[1:])

        # Handle special cases
        if resource in ["task", "tasks"]:
            return "tasks"
        elif resource in ["content", "contents"]:
            return "content"
        elif resource in ["market", "markets", "competitor", "competitors"]:
            return "market_analysis"
        elif resource in ["financial", "budget", "cost", "roi"]:
            return "financial_analysis"
        elif resource in ["compliance", "legal"]:
            return "compliance"
        elif resource in ["publish", "publishing"]:
            return "publishing"

        # Default: pluralize resource name
        if not resource.endswith("s"):
            resource += "s"

        return resource
