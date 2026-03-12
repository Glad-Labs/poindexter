"""
Unit tests for services/system_knowledge_rag.py

Tests SystemKnowledgeRAG: initialization, section parsing, structured question
matching, semantic search, keyword retrieval, and module-level factory.
No filesystem access is needed for structured-question tests; all raw-KB tests
use a patch to inject synthetic markdown content.
"""

from unittest.mock import patch, mock_open

import pytest

from services.system_knowledge_rag import (
    KnowledgeResult,
    SystemKnowledgeRAG,
    get_system_knowledge_rag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_KB = """\
# Glad Labs System Knowledge

## Technology Stack
Python is the primary backend language. FastAPI is used as the web framework.

## Specialized Agent System
There are five specialized agents available for different tasks.

## LLM Provider Integration
Five LLM providers are supported: Ollama, Anthropic, OpenAI, Google, HuggingFace.

## Database
PostgreSQL on port 5432 stores all persistent data.

## Architecture
Three ports: 8000 for backend, 3000 for public site, 3001 for admin.

## Workflow System
Phase-based execution with automatic input/output mapping.

## Quality Assessment Framework
Six quality dimensions scored from 0 to 100.
"""


def make_rag_with_kb(content: str = SAMPLE_KB) -> SystemKnowledgeRAG:
    """Return a SystemKnowledgeRAG instance backed by the given content string."""
    with patch("builtins.open", mock_open(read_data=content)):
        with patch("pathlib.Path.exists", return_value=True):
            return SystemKnowledgeRAG()


def make_empty_rag() -> SystemKnowledgeRAG:
    """Return a SystemKnowledgeRAG instance with no knowledge base file."""
    with patch("pathlib.Path.exists", return_value=False):
        return SystemKnowledgeRAG()


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestSystemKnowledgeRAGInit:
    def test_initialized_true_when_kb_loaded(self):
        rag = make_rag_with_kb()
        assert rag.is_initialized is True

    def test_initialized_false_when_no_file(self):
        rag = make_empty_rag()
        assert rag.is_initialized is False

    def test_sections_parsed_when_kb_loaded(self):
        rag = make_rag_with_kb()
        assert len(rag.sections) > 0

    def test_sections_empty_when_no_file(self):
        rag = make_empty_rag()
        assert rag.sections == {}

    def test_knowledge_base_stored(self):
        rag = make_rag_with_kb()
        assert "Glad Labs" in rag.knowledge_base


# ---------------------------------------------------------------------------
# _parse_sections
# ---------------------------------------------------------------------------


class TestParseSections:
    def test_parses_h2_sections(self):
        rag = make_rag_with_kb()
        assert "Technology Stack" in rag.sections
        assert "Database" in rag.sections
        assert "Architecture" in rag.sections

    def test_section_content_correct(self):
        rag = make_rag_with_kb()
        assert "FastAPI" in rag.sections["Technology Stack"]

    def test_empty_kb_yields_no_sections(self):
        rag = make_empty_rag()
        assert rag.sections == {}


# ---------------------------------------------------------------------------
# _check_structured_questions
# ---------------------------------------------------------------------------


class TestStructuredQuestions:
    def test_programming_language_query(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("what programming language is used")
        assert result is not None
        assert result.is_structured_answer is True
        assert result.confidence >= 0.9
        assert "Python" in result.content

    def test_agent_types_query(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("what agent types are available")
        assert result is not None
        assert "agent" in result.content.lower()

    def test_llm_provider_query(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("what LLM providers are supported")
        assert result is not None
        assert "Ollama" in result.content

    def test_database_query(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("what database is used")
        assert result is not None
        assert "PostgreSQL" in result.content

    def test_port_query(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("what port does the backend listen on")
        assert result is not None
        assert "8000" in result.content

    def test_workflow_query(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("how does the workflow execute phases")
        assert result is not None
        assert result.source_section == "Workflow System"

    def test_quality_query(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("what is the quality assessment score")
        assert result is not None
        assert result.source_section == "Quality Assessment Framework"

    def test_unrecognized_query_returns_none(self):
        rag = make_rag_with_kb()
        result = rag._check_structured_questions("random gibberish xyz123")
        assert result is None


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------


class TestRetrieve:
    def test_returns_none_when_not_initialized(self):
        rag = make_empty_rag()
        result = rag.retrieve("programming language")
        assert result is None

    def test_returns_structured_result_for_known_query(self):
        rag = make_rag_with_kb()
        result = rag.retrieve("what programming language is used")
        assert result is not None
        assert result.is_structured_answer is True

    def test_returns_knowledge_result_type(self):
        rag = make_rag_with_kb()
        result = rag.retrieve("programming language")
        assert isinstance(result, KnowledgeResult)

    def test_confidence_field_present(self):
        rag = make_rag_with_kb()
        result = rag.retrieve("Python FastAPI")
        assert result is not None
        assert 0 <= result.confidence <= 1.0

    def test_returns_fallback_for_generic_query(self):
        """Even an unrecognized query returns the intro section as fallback."""
        rag = make_rag_with_kb()
        result = rag.retrieve("completely_unrelated_nonsense_xyz")
        # Should return something (fallback)
        assert result is not None

    def test_above_threshold_returns_result(self):
        rag = make_rag_with_kb()
        # A query that matches the Technology Stack section
        result = rag.retrieve("Python FastAPI backend language", confidence_threshold=0.0)
        assert result is not None


# ---------------------------------------------------------------------------
# retrieve_by_keyword
# ---------------------------------------------------------------------------


class TestRetrieveByKeyword:
    def test_empty_keywords_returns_none(self):
        rag = make_rag_with_kb()
        result = rag.retrieve_by_keyword([])
        assert result is None

    def test_no_sections_returns_none(self):
        rag = make_empty_rag()
        result = rag.retrieve_by_keyword(["python"])
        assert result is None

    def test_matching_keyword_returns_result(self):
        rag = make_rag_with_kb()
        result = rag.retrieve_by_keyword(["postgresql"])
        assert result is not None
        assert result.source_section == "Database"

    def test_result_has_confidence(self):
        rag = make_rag_with_kb()
        result = rag.retrieve_by_keyword(["FastAPI"])
        assert result is not None
        assert result.confidence > 0

    def test_best_section_selected(self):
        rag = make_rag_with_kb()
        result = rag.retrieve_by_keyword(["Ollama", "Anthropic", "OpenAI"])
        assert result is not None
        assert result.source_section == "LLM Provider Integration"


# ---------------------------------------------------------------------------
# get_section / list_sections
# ---------------------------------------------------------------------------


class TestGetSectionAndListSections:
    def test_get_existing_section(self):
        rag = make_rag_with_kb()
        content = rag.get_section("Database")
        assert content is not None
        assert "PostgreSQL" in content

    def test_get_nonexistent_section_returns_none(self):
        rag = make_rag_with_kb()
        assert rag.get_section("Nonexistent Section") is None

    def test_list_sections_returns_list(self):
        rag = make_rag_with_kb()
        sections = rag.list_sections()
        assert isinstance(sections, list)
        assert "Technology Stack" in sections
        assert "Database" in sections

    def test_list_sections_empty_when_no_kb(self):
        rag = make_empty_rag()
        assert rag.list_sections() == []


# ---------------------------------------------------------------------------
# search_multiple
# ---------------------------------------------------------------------------


class TestSearchMultiple:
    def test_empty_sections_returns_empty_list(self):
        rag = make_empty_rag()
        results = rag.search_multiple("python")
        assert results == []

    def test_returns_list_of_knowledge_results(self):
        rag = make_rag_with_kb()
        results = rag.search_multiple("Python FastAPI agent")
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, KnowledgeResult)

    def test_limit_respected(self):
        rag = make_rag_with_kb()
        results = rag.search_multiple("the and is", limit=2)
        assert len(results) <= 2

    def test_results_sorted_by_confidence_desc(self):
        rag = make_rag_with_kb()
        results = rag.search_multiple("database PostgreSQL port", limit=3)
        confidences = [r.confidence for r in results]
        assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_returns_lowercase_tokens(self):
        rag = make_rag_with_kb()
        tokens = rag._tokenize("Python FastAPI")
        assert "python" in tokens
        assert "fastapi" in tokens

    def test_filters_short_tokens(self):
        rag = make_rag_with_kb()
        tokens = rag._tokenize("a is to be")
        assert "a" not in tokens
        assert "is" not in tokens

    def test_handles_empty_string(self):
        rag = make_rag_with_kb()
        tokens = rag._tokenize("")
        assert tokens == []


# ---------------------------------------------------------------------------
# get_system_knowledge_rag (factory)
# ---------------------------------------------------------------------------


class TestGetSystemKnowledgeRAG:
    def test_returns_instance(self):
        import services.system_knowledge_rag as mod
        # Reset singleton so test is isolated
        mod._system_knowledge_rag = None
        with patch("pathlib.Path.exists", return_value=False):
            rag = get_system_knowledge_rag()
        assert isinstance(rag, SystemKnowledgeRAG)

    def test_returns_same_instance_on_second_call(self):
        import services.system_knowledge_rag as mod
        mod._system_knowledge_rag = None
        with patch("pathlib.Path.exists", return_value=False):
            rag1 = get_system_knowledge_rag()
            rag2 = get_system_knowledge_rag()
        assert rag1 is rag2
