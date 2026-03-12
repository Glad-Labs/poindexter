"""
Unit tests for agents/registry.py — AgentRegistry
"""

import pytest
from unittest.mock import MagicMock
from agents.registry import AgentRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    """Return a fresh AgentRegistry for each test."""
    return AgentRegistry()


class _FakeAgent:
    """Minimal stand-in for an agent class."""
    pass


class _OtherAgent:
    pass


# ---------------------------------------------------------------------------
# register() — happy path
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_stores_metadata(self, registry):
        registry.register(
            name="content_agent",
            agent_class=_FakeAgent,
            category="content",
            phases=["research", "draft"],
            capabilities=["web_search"],
            description="Test agent",
            version="2.0",
        )
        agent = registry.get("content_agent")
        assert agent is not None
        assert agent["name"] == "content_agent"
        assert agent["class"] is _FakeAgent
        assert agent["category"] == "content"
        assert "research" in agent["phases"]
        assert "draft" in agent["phases"]
        assert "web_search" in agent["capabilities"]
        assert agent["description"] == "Test agent"
        assert agent["version"] == "2.0"

    def test_register_indexes_by_category(self, registry):
        registry.register("a", _FakeAgent, category="content")
        assert "a" in registry.list_agents(category="content")

    def test_register_indexes_by_phase(self, registry):
        registry.register("a", _FakeAgent, phases=["research"])
        assert "a" in registry.list_by_phase("research")

    def test_register_with_defaults(self, registry):
        registry.register("minimal", _FakeAgent)
        agent = registry.get("minimal")
        assert agent["category"] == "utility"
        assert agent["phases"] == []
        assert agent["capabilities"] == []
        assert agent["description"] == ""
        assert agent["version"] == "1.0"

    def test_register_overwrites_duplicate(self, registry):
        registry.register("dup", _FakeAgent)
        registry.register("dup", _OtherAgent)
        assert registry.get_agent_class("dup") is _OtherAgent

    def test_register_unknown_category_still_stores(self, registry):
        """Registering with an unknown category stores agent but doesn't index by category."""
        registry.register("x", _FakeAgent, category="unknown_category")
        assert registry.get("x") is not None
        # Unknown category not in the predefined dict, so no category index
        assert "x" not in registry.list_agents(category="unknown_category")

    def test_register_multiple_phases(self, registry):
        registry.register("multi", _FakeAgent, phases=["p1", "p2", "p3"])
        assert "multi" in registry.list_by_phase("p1")
        assert "multi" in registry.list_by_phase("p2")
        assert "multi" in registry.list_by_phase("p3")


# ---------------------------------------------------------------------------
# get() / get_agent_class() / get_metadata()
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_returns_none_for_missing(self, registry):
        assert registry.get("does_not_exist") is None

    def test_get_agent_class_returns_class(self, registry):
        registry.register("cls", _FakeAgent)
        assert registry.get_agent_class("cls") is _FakeAgent

    def test_get_agent_class_returns_none_for_missing(self, registry):
        assert registry.get_agent_class("missing") is None

    def test_get_metadata_same_as_get(self, registry):
        registry.register("m", _FakeAgent, description="desc")
        assert registry.get_metadata("m") == registry.get("m")

    def test_get_metadata_returns_none_for_missing(self, registry):
        assert registry.get_metadata("nope") is None


# ---------------------------------------------------------------------------
# list_agents()
# ---------------------------------------------------------------------------


class TestListAgents:
    def test_list_all_agents(self, registry):
        registry.register("a", _FakeAgent, category="content")
        registry.register("b", _OtherAgent, category="financial")
        names = registry.list_agents()
        assert "a" in names
        assert "b" in names

    def test_list_agents_by_category(self, registry):
        registry.register("c", _FakeAgent, category="content")
        registry.register("f", _OtherAgent, category="financial")
        assert "c" in registry.list_agents(category="content")
        assert "f" not in registry.list_agents(category="content")

    def test_list_agents_empty_category(self, registry):
        assert registry.list_agents(category="market") == []

    def test_list_by_phase_returns_correct_agents(self, registry):
        registry.register("a", _FakeAgent, phases=["draft"])
        registry.register("b", _OtherAgent, phases=["research"])
        assert registry.list_by_phase("draft") == ["a"]
        assert registry.list_by_phase("research") == ["b"]

    def test_list_by_phase_missing_returns_empty(self, registry):
        assert registry.list_by_phase("nonexistent_phase") == []

    def test_list_by_capability(self, registry):
        registry.register("a", _FakeAgent, capabilities=["web_search", "llm"])
        registry.register("b", _OtherAgent, capabilities=["llm"])
        assert "a" in registry.list_by_capability("web_search")
        assert "b" not in registry.list_by_capability("web_search")
        assert "a" in registry.list_by_capability("llm")
        assert "b" in registry.list_by_capability("llm")

    def test_list_by_capability_missing_returns_empty(self, registry):
        assert registry.list_by_capability("no_such_cap") == []


# ---------------------------------------------------------------------------
# list_categories()
# ---------------------------------------------------------------------------


class TestListCategories:
    def test_empty_categories_excluded(self, registry):
        cats = registry.list_categories()
        # Fresh registry has no agents
        assert cats == {}

    def test_populated_categories_included(self, registry):
        registry.register("a", _FakeAgent, category="content")
        registry.register("b", _OtherAgent, category="financial")
        cats = registry.list_categories()
        assert "content" in cats
        assert "financial" in cats
        assert "compliance" not in cats  # empty


# ---------------------------------------------------------------------------
# get_capabilities() / get_phases()
# ---------------------------------------------------------------------------


class TestCapabilitiesAndPhases:
    def test_get_capabilities(self, registry):
        registry.register("a", _FakeAgent, capabilities=["cap1", "cap2"])
        assert registry.get_capabilities("a") == ["cap1", "cap2"]

    def test_get_capabilities_missing_returns_empty(self, registry):
        assert registry.get_capabilities("missing") == []

    def test_get_phases(self, registry):
        registry.register("a", _FakeAgent, phases=["ph1", "ph2"])
        assert registry.get_phases("a") == ["ph1", "ph2"]

    def test_get_phases_missing_returns_empty(self, registry):
        assert registry.get_phases("missing") == []


# ---------------------------------------------------------------------------
# get_serializable_metadata() / list_all_with_metadata()
# ---------------------------------------------------------------------------


class TestSerializableMetadata:
    def test_get_serializable_excludes_class(self, registry):
        registry.register("s", _FakeAgent, description="desc", version="3.0")
        meta = registry.get_serializable_metadata("s")
        assert meta is not None
        assert "class" not in meta
        assert meta["name"] == "s"
        assert meta["description"] == "desc"
        assert meta["version"] == "3.0"

    def test_get_serializable_returns_none_for_missing(self, registry):
        assert registry.get_serializable_metadata("nope") is None

    def test_list_all_with_metadata_excludes_class(self, registry):
        registry.register("a", _FakeAgent)
        all_meta = registry.list_all_with_metadata()
        assert len(all_meta) == 1
        assert "class" not in all_meta[0]

    def test_list_all_with_metadata_empty(self, registry):
        assert registry.list_all_with_metadata() == []

    def test_list_all_with_metadata_multiple(self, registry):
        registry.register("a", _FakeAgent)
        registry.register("b", _OtherAgent)
        all_meta = registry.list_all_with_metadata()
        names = [m["name"] for m in all_meta]
        assert "a" in names
        assert "b" in names


# ---------------------------------------------------------------------------
# De-duplication: same agent not added twice to phase/category indexes
# ---------------------------------------------------------------------------


class TestNoDuplication:
    def test_category_no_duplicates_on_overwrite(self, registry):
        registry.register("dup", _FakeAgent, category="content")
        registry.register("dup", _OtherAgent, category="content")
        cat_list = registry.list_agents(category="content")
        assert cat_list.count("dup") == 1

    def test_phase_no_duplicates_on_overwrite(self, registry):
        registry.register("dup", _FakeAgent, phases=["draft"])
        registry.register("dup", _OtherAgent, phases=["draft"])
        phase_list = registry.list_by_phase("draft")
        assert phase_list.count("dup") == 1
