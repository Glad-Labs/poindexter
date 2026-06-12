"""Contract test: PipelineState must declare the three seo_refresh channels.

These channels are needed so build_graph_from_spec's _validate_graph_schema
(#753) accepts seo_refresh atoms' requires/produces without raising ValueError.
Without the declarations LangGraph silently drops any state updates whose keys
are not in the TypedDict schema — declaring them here makes that a loud error
at graph compile time instead.

Issue: Glad-Labs/poindexter#763 (SEO Harvest Loop Phase 2).
"""

from services.template_runner import PipelineState


def test_seo_refresh_channels_declared():
    keys = set(PipelineState.__annotations__)
    for k in ("target_query", "seo_opportunity_id", "seo_refresh_scope"):
        assert k in keys, f"{k} must be declared so build_graph_from_spec accepts it"
