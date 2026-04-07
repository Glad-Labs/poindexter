"""
Agent Initialization - Register all agents in the AgentRegistry at startup

This module registers agents from the agents/ folder with the central
AgentRegistry, making them discoverable and selectable at runtime.
"""

from typing import Optional

from agents.registry import AgentRegistry, get_agent_registry
from services.logger_config import get_logger

logger = get_logger(__name__)


def register_all_agents(registry: Optional[AgentRegistry] = None) -> AgentRegistry:
    """
    Register all agents with the AgentRegistry.

    Args:
        registry: Optional AgentRegistry instance (uses global if not provided)

    Returns:
        Initialized AgentRegistry with all agents registered
    """
    if registry is None:
        registry = get_agent_registry()

    try:
        # Import agent classes
        from agents.content_agent.agents.creative_agent import CreativeAgent
        from agents.content_agent.agents.postgres_image_agent import (
            PostgreSQLImageAgent as ImageAgent,
        )
        from agents.content_agent.agents.postgres_publishing_agent import PostgreSQLPublishingAgent
        from agents.content_agent.agents.qa_agent import QAAgent
        from agents.content_agent.agents.research_agent import ResearchAgent

        # Register content agents
        registry.register(
            name="research_agent",
            agent_class=ResearchAgent,
            category="content",
            phases=["research"],
            capabilities=["web_search", "information_gathering", "summarization"],
            description="Gathers research and background information for content generation",
            version="1.0",
        )

        registry.register(
            name="creative_agent",
            agent_class=CreativeAgent,
            category="content",
            phases=["draft", "refine"],
            capabilities=["content_generation", "writing", "style_adaptation"],
            description="Generates and refines creative content with style guidance",
            version="1.0",
        )

        registry.register(
            name="qa_agent",
            agent_class=QAAgent,
            category="content",
            phases=["assess"],
            capabilities=["quality_assessment", "feedback_generation", "critique"],
            description="Evaluates content quality and provides improvement feedback",
            version="1.0",
        )

        registry.register(
            name="imaging_agent",
            agent_class=ImageAgent,
            category="content",
            phases=["image_selection"],
            capabilities=["image_search", "metadata_generation"],
            description="Finds and optimizes images for content",
            version="1.0",
        )

        registry.register(
            name="publishing_agent",
            agent_class=PostgreSQLPublishingAgent,
            category="content",
            phases=["finalize"],
            capabilities=["content_formatting", "seo_optimization", "publishing"],
            description="Formats and publishes content to PostgreSQL",
            version="1.0",
        )

        logger.info("✅ Registered 5 content agents")

    except ImportError as e:
        logger.warning(f"⚠️  Could not import content agents: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ Failed to register content agents: {e}", exc_info=True)

    try:
        # Register financial agent
        from agents.financial_agent import FinancialAgent

        registry.register(
            name="financial_agent",
            agent_class=FinancialAgent,
            category="financial",
            phases=["financial_analysis"],
            capabilities=["data_analysis", "financial_calculations", "reporting"],
            description="Analyzes financial metrics and generates reports",
            version="1.0",
        )

        logger.info("✅ Registered financial agent")

    except ImportError as e:
        logger.warning(f"⚠️  Could not import financial agent: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ Failed to register financial agent: {e}", exc_info=True)

    try:
        # Register market insight agent
        from agents.market_insight_agent import MarketInsightAgent

        registry.register(
            name="market_insight_agent",
            agent_class=MarketInsightAgent,
            category="market",
            phases=["market_analysis"],
            capabilities=["market_research", "competitive_analysis", "trend_analysis"],
            description="Analyzes market trends and competitive landscape",
            version="1.0",
        )

        logger.info("✅ Registered market insight agent")

    except ImportError as e:
        logger.warning(f"⚠️  Could not import market insight agent: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ Failed to register market insight agent: {e}", exc_info=True)

    try:
        # Register compliance agent
        from agents.compliance_agent import ComplianceAgent

        registry.register(
            name="compliance_agent",
            agent_class=ComplianceAgent,
            category="compliance",
            phases=["compliance_check"],
            capabilities=["compliance_checking", "risk_assessment", "legal_review"],
            description="Checks compliance and assesses legal/regulatory risks",
            version="1.0",
        )

        logger.info("✅ Registered compliance agent")

    except ImportError as e:
        logger.warning(f"⚠️  Could not import compliance agent: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ Failed to register compliance agent: {e}", exc_info=True)

    logger.info(
        f"✅ Agent/Service registration complete: {len(registry)} agents/services registered"
    )
    return registry
