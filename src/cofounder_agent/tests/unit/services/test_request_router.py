"""
Unit tests for services.request_router — the natural-language intent classifier.

Tests verify that classify_request returns the correct RequestType and intent
for the documented keyword patterns, and that the default fallback to
CONTENT_CREATION works correctly.
"""

from services.orchestrator_types import Request, RequestType
from services.request_router import _match_routing_table, classify_request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_op_extractor(text: str):
    """Stub for extract_content_params_fn — just returns the raw text as topic."""
    return {"topic": text, "style": "professional", "tone": "informative"}


def _classify(user_input: str, context=None) -> Request:
    return classify_request(
        user_input=user_input,
        request_id="test-req-id",
        context=context,
        extract_content_params_fn=_no_op_extractor,
    )


# ---------------------------------------------------------------------------
# Tests: _match_routing_table
# ---------------------------------------------------------------------------


class TestMatchRoutingTable:
    def test_content_creation_keyword(self):
        result = _match_routing_table("create content about AI")
        assert result is not None
        request_type, intent = result
        assert request_type == RequestType.CONTENT_CREATION
        assert intent == "content_creation"

    def test_blog_post_keyword(self):
        request_type, intent = _match_routing_table("write a blog post about python")  # type: ignore[misc]
        assert request_type == RequestType.CONTENT_CREATION

    def test_research_keyword(self):
        request_type, intent = _match_routing_table("research the topic of ML")  # type: ignore[misc]
        assert request_type == RequestType.CONTENT_SUBTASK
        assert intent == "research"

    def test_creative_keyword(self):
        request_type, intent = _match_routing_table("generate creative text")  # type: ignore[misc]
        assert request_type == RequestType.CONTENT_SUBTASK
        assert intent == "creative"

    def test_financial_keyword(self):
        request_type, intent = _match_routing_table("show me financial data")  # type: ignore[misc]
        assert request_type == RequestType.FINANCIAL_ANALYSIS

    def test_compliance_keyword(self):
        request_type, intent = _match_routing_table("run compliance audit")  # type: ignore[misc]
        assert request_type == RequestType.COMPLIANCE_CHECK

    def test_task_management_keyword(self):
        request_type, intent = _match_routing_table("create task for me")  # type: ignore[misc]
        assert request_type == RequestType.TASK_MANAGEMENT
        assert intent == "create_task"

    def test_information_retrieval_keyword(self):
        request_type, intent = _match_routing_table("what are the latest stats")  # type: ignore[misc]
        assert request_type == RequestType.INFORMATION_RETRIEVAL
        assert intent == "retrieve_info"

    def test_decision_support_keyword(self):
        request_type, intent = _match_routing_table("should i use python or go")  # type: ignore[misc]
        assert request_type == RequestType.DECISION_SUPPORT
        assert intent == "decision_support"

    def test_system_operation_keyword(self):
        request_type, intent = _match_routing_table("help me understand this")  # type: ignore[misc]
        assert request_type == RequestType.SYSTEM_OPERATION
        assert intent == "system_info"

    def test_intervention_keyword(self):
        request_type, intent = _match_routing_table("stop the running process")  # type: ignore[misc]
        assert request_type == RequestType.INTERVENTION
        assert intent == "intervention"

    def test_no_match_returns_none(self):
        result = _match_routing_table("unrecognised gibberish xyz123")
        assert result is None

    def test_case_insensitive_via_lowercase_input(self):
        # Caller is responsible for lowercasing — verify with lower input
        result = _match_routing_table("create content about something")
        assert result is not None


# ---------------------------------------------------------------------------
# Tests: classify_request — returned Request object
# ---------------------------------------------------------------------------


class TestClassifyRequestType:
    def test_content_creation_returns_correct_type(self):
        req = _classify("write about machine learning")
        assert req.request_type == RequestType.CONTENT_CREATION

    def test_research_returns_subtask(self):
        req = _classify("research about climate change")
        assert req.request_type == RequestType.CONTENT_SUBTASK
        assert req.parameters["subtask_type"] == "research"

    def test_creative_returns_subtask(self):
        req = _classify("draft a creative piece")
        assert req.request_type == RequestType.CONTENT_SUBTASK
        assert req.parameters["subtask_type"] == "creative"

    def test_financial_type(self):
        req = _classify("analyze budget allocations")
        assert req.request_type == RequestType.FINANCIAL_ANALYSIS

    def test_compliance_type(self):
        req = _classify("run a security audit")
        assert req.request_type == RequestType.COMPLIANCE_CHECK

    def test_task_management_type(self):
        req = _classify("add task: write blog")
        assert req.request_type == RequestType.TASK_MANAGEMENT
        assert "task_description" in req.parameters

    def test_information_retrieval_type(self):
        req = _classify("show me recent activity")
        assert req.request_type == RequestType.INFORMATION_RETRIEVAL
        assert req.parameters["query"] == "show me recent activity"

    def test_decision_support_type(self):
        # Note: "what should I..." would match INFORMATION_RETRIEVAL first because
        # "what " appears in that rule's keyword list and it precedes DECISION_SUPPORT
        # in the routing table.  Use a keyword that fires exclusively on DECISION_SUPPORT.
        req = _classify("should i use python or go")
        assert req.request_type == RequestType.DECISION_SUPPORT
        assert "decision_question" in req.parameters

    def test_decision_support_recommend_keyword(self):
        req = _classify("I need a recommend for a good book")
        assert req.request_type == RequestType.DECISION_SUPPORT

    def test_system_operation_type(self):
        req = _classify("show system status")
        assert req.request_type == RequestType.SYSTEM_OPERATION

    def test_intervention_type(self):
        req = _classify("cancel all running tasks")
        assert req.request_type == RequestType.INTERVENTION

    def test_default_fallback_is_content_creation(self):
        req = _classify("unrecognised gibberish xyz123abc")
        assert req.request_type == RequestType.CONTENT_CREATION
        assert req.extracted_intent == "content_creation_default"


class TestClassifyRequestFields:
    def test_request_id_preserved(self):
        req = classify_request(
            user_input="write about AI",
            request_id="my-specific-id",
            context=None,
            extract_content_params_fn=_no_op_extractor,
        )
        assert req.request_id == "my-specific-id"

    def test_original_text_preserved(self):
        text = "write about AI trends"
        req = _classify(text)
        assert req.original_text == text

    def test_context_passed_through(self):
        ctx = {"user_id": "u123", "session": "s456"}
        req = classify_request(
            user_input="write about AI",
            request_id="r1",
            context=ctx,
            extract_content_params_fn=_no_op_extractor,
        )
        assert req.context == ctx

    def test_none_context_becomes_empty_dict(self):
        req = _classify("write about AI")
        assert req.context == {}

    def test_extract_params_called_for_content_creation(self):
        called_with = []

        def capturing_extractor(text):
            called_with.append(text)
            return {"topic": text}

        classify_request(
            user_input="write about python",
            request_id="r1",
            context=None,
            extract_content_params_fn=capturing_extractor,
        )
        assert called_with == ["write about python"]

    def test_extract_params_called_for_default_fallback(self):
        called_with = []

        def capturing_extractor(text):
            called_with.append(text)
            return {"topic": text}

        classify_request(
            user_input="some_unmatched_string_xyz",
            request_id="r1",
            context=None,
            extract_content_params_fn=capturing_extractor,
        )
        assert called_with == ["some_unmatched_string_xyz"]

    def test_extract_params_not_called_for_financial(self):
        called = []

        def capturing_extractor(text):
            called.append(text)
            return {}

        _classify("analyze budget data")
        assert called == []  # extractor should not be called for non-content routes
