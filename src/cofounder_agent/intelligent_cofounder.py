"""
Intelligent AI Co-Founder for GLAD Labs

A comprehensive AI business partner that understands all aspects of your business,
provides strategic insights, manages operations, and helps make informed decisions.

This is the core intelligence system for small business and entrepreneur AI automation.
"""

import asyncio
import logging
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum

# Add MCP and agent paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'agents', 'content_agent'))

from mcp.client_manager import MCPClientManager
from mcp.servers.ai_model_server import AIModelServer
from mcp.servers.strapi_server import StrapiMCPServer
from .business_intelligence import BusinessIntelligenceSystem
from .memory_system import AIMemorySystem, MemoryType, ImportanceLevel


class BusinessArea(str, Enum):
    """Key business areas the AI co-founder manages"""
    CONTENT_STRATEGY = "content_strategy"
    MARKETING = "marketing"
    OPERATIONS = "operations"
    ANALYTICS = "analytics"
    FINANCE = "finance"
    GROWTH = "growth"
    TECHNOLOGY = "technology"
    BRANDING = "branding"


class InsightType(str, Enum):
    """Types of business insights"""
    OPPORTUNITY = "opportunity"
    WARNING = "warning"
    RECOMMENDATION = "recommendation"
    TREND = "trend"
    PREDICTION = "prediction"
    ANALYSIS = "analysis"


@dataclass
class BusinessInsight:
    """Structured business insight"""
    type: InsightType
    area: BusinessArea
    title: str
    description: str
    confidence: float  # 0.0 to 1.0
    priority: int  # 1 (low) to 5 (critical)
    actionable: bool
    created_at: datetime
    data_sources: List[str]
    recommendations: Optional[List[str]] = None


@dataclass
class BusinessContext:
    """Comprehensive business context"""
    # Core business info
    business_name: str = "GLAD Labs"
    business_type: str = "AI Content & Marketing Platform"
    target_market: str = "Small businesses and entrepreneurs"
    
    # Current metrics
    total_content_pieces: int = 0
    active_campaigns: int = 0
    monthly_costs: float = 0.0
        revenue_streams: Optional[List[str]] = None
    
    # Performance data
        content_performance: Optional[Dict[str, Any]] = None
        cost_optimization: Optional[Dict[str, Any]] = None
        growth_metrics: Optional[Dict[str, Any]] = None
    
    # Strategic context
        current_goals: Optional[List[str]] = None
        challenges: Optional[List[str]] = None
        opportunities: Optional[List[str]] = None
    
    # Technical context
        active_services: Optional[List[str]] = None
        system_health: Optional[Dict[str, Any]] = None
    
    # Market context
        competitor_analysis: Optional[Dict[str, Any]] = None
        market_trends: Optional[List[str]] = None
    
        last_updated: Optional[datetime] = None


class IntelligentCoFounder:
    """
    AI Co-Founder: Comprehensive business intelligence and management system.
    
    This AI partner:
    1. Understands all aspects of your business
    2. Provides strategic insights and recommendations
    3. Manages day-to-day operations
    4. Predicts trends and opportunities
    5. Optimizes costs and performance
    6. Guides business growth
    """
    
    def __init__(self, business_name: str = "GLAD Labs"):
        self.business_name = business_name
        self.logger = logging.getLogger("intelligent_cofounder")
        
        # Core systems
        self.mcp_manager = MCPClientManager()
        self.business_context = BusinessContext(business_name=business_name)
        
        # Enhanced intelligence systems
        self.business_intelligence = BusinessIntelligenceSystem()
        self.memory_system = AIMemorySystem()
        
        # Memory and knowledge
        self.conversation_memory: List[Dict[str, Any]] = []
        self.business_knowledge: Dict[str, Any] = {}
        self.insights_history: List[BusinessInsight] = []
        
        # AI personality and expertise
        self.personality_context = self._build_personality_context()
        self.expertise_areas = self._define_expertise_areas()
        
        # State management
        self.initialized = False
        self.last_context_update = None
    
    async def initialize(self) -> bool:
        """Initialize the AI co-founder with full business context"""
        try:
            self.logger.info("Initializing Intelligent AI Co-Founder...")
            
            # Initialize MCP infrastructure
            await self._setup_mcp_systems()
            
                # Initialize business intelligence system
                await self.business_intelligence.collect_all_metrics()
            
                # Initialize memory system (loads existing memories)
                # Memory system initialization is automatic in __init__
            
            # Gather comprehensive business context
            await self._gather_business_context()
            
                # Store initialization in memory
                await self.memory_system.store_memory(
                    content=f"AI Co-Founder initialized for {self.business_name}",
                    memory_type=MemoryType.BUSINESS_FACT,
                    importance=ImportanceLevel.HIGH,
                    tags=["initialization", "system_startup"],
                    metadata={"business_name": self.business_name, "timestamp": datetime.now().isoformat()}
                )
            
            # Generate initial business assessment
            await self._perform_initial_assessment()
            
            self.initialized = True
            self.logger.info("AI Co-Founder initialization complete")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize AI Co-Founder: {e}")
            return False
    
    async def _setup_mcp_systems(self):
        """Set up MCP servers and capabilities"""
        # Register AI model server
        ai_server = AIModelServer()
        await self.mcp_manager.register_server("ai-models", ai_server)
        
        # Register Strapi CMS server
        strapi_server = StrapiMCPServer()
        await self.mcp_manager.register_server("strapi-cms", strapi_server)
        
        # Future: Register additional business intelligence servers
        # - Analytics server (Google Analytics, social media metrics)
        # - Financial server (expense tracking, revenue analysis)
        # - Market intelligence server (competitor analysis, trends)
        # - Customer intelligence server (feedback, behavior analysis)
    
    async def _gather_business_context(self):
        """Gather comprehensive business context from all sources"""
        context_data = {}
        
        try:
            # Content management context
            content_stats = await self.mcp_manager.call_tool("get_content_stats", {})
            if content_stats.get("success"):
                stats = content_stats.get("stats", {})
                context_data["content"] = {
                    "total_posts": stats.get("total_published_posts", 0),
                    "cms_status": stats.get("strapi_connection", "unknown"),
                    "last_update": stats.get("last_updated", "unknown")
                }
            
            # AI model capabilities context
            models_info = await self.mcp_manager.call_tool("get_available_models", {})
            if models_info.get("available_models"):
                context_data["ai_capabilities"] = {
                    "total_models": sum(len(models) for models in models_info["available_models"].values()),
                    "providers": models_info.get("providers_available", {}),
                    "cost_tiers": list(models_info.get("available_models", {}).keys())
                }
            
            # System health context
            server_status = await self.mcp_manager.get_server_status()
            context_data["system"] = {
                "total_servers": server_status.get("total_servers", 0),
                "active_tools": server_status.get("total_tools", 0),
                "system_health": "operational" if server_status.get("total_servers", 0) > 0 else "limited"
            }
            
            # Update business context
            self.business_context.total_content_pieces = context_data.get("content", {}).get("total_posts", 0)
            self.business_context.active_services = [
                name for name, info in server_status.get("servers", {}).items() 
                if info.get("status") == "connected"
            ]
            self.business_context.system_health = context_data.get("system", {})
            self.business_context.last_updated = datetime.now()
            
            self._update_business_knowledge("current_context", context_data)
            
        except Exception as e:
            self.logger.error(f"Error gathering business context: {e}")
    
    async def _load_business_memory(self):
        """Load persistent business memory and knowledge"""
        # For now, initialize with default knowledge
        # In production, this would load from persistent storage
        
        self.business_knowledge.update({
            "company_vision": "Simplify AI agentic pipelines for small businesses and entrepreneurs",
            "target_customers": [
                "Small business owners",
                "Solo entrepreneurs", 
                "Content creators",
                "Digital marketers",
                "Bloggers and influencers"
            ],
            "core_competencies": [
                "AI-powered content generation",
                "Automated marketing workflows",
                "Cost-optimized AI operations",
                "Multi-platform content distribution",
                "Business intelligence and analytics"
            ],
            "growth_strategy": [
                "Build fleet of blog sites and social media personalities",
                "Package as SaaS tool for entrepreneurs",
                "Low-barrier entry for non-technical users",
                "Focus on ROI and business results"
            ]
        })
    
    async def _perform_initial_assessment(self):
        """Perform initial business assessment and generate insights"""
        insights = []
        
        # Assess content production capability
        if self.business_context.total_content_pieces < 10:
            insights.append(BusinessInsight(
                type=InsightType.RECOMMENDATION,
                area=BusinessArea.CONTENT_STRATEGY,
                title="Accelerate Content Production",
                description="Current content library is limited. Consider implementing automated content generation workflows to build a substantial content base.",
                confidence=0.9,
                priority=4,
                actionable=True,
                created_at=datetime.now(),
                data_sources=["content_management_system"],
                recommendations=[
                    "Set up automated daily content generation",
                    "Create content calendar with diverse topics",
                    "Implement SEO optimization in content workflow"
                ]
            ))
        
        # Assess AI cost optimization
            if self.business_context.system_health and self.business_context.system_health.get("system_health") == "operational":
                insights.append(BusinessInsight(
                type=InsightType.OPPORTUNITY,
                area=BusinessArea.OPERATIONS,
                title="AI Cost Optimization Active",
                description="MCP-based AI infrastructure is operational, enabling cost-optimized content generation across multiple providers.",
                confidence=0.95,
                priority=3,
                actionable=True,
                created_at=datetime.now(),
                data_sources=["mcp_system_status"],
                recommendations=[
                    "Monitor AI usage costs daily",
                    "Use local models for development tasks",
                    "Implement usage budgets and alerts"
                ]
            ))
        
        # Assess growth readiness
        insights.append(BusinessInsight(
            type=InsightType.ANALYSIS,
            area=BusinessArea.GROWTH,
            title="SaaS Product Development Readiness",
            description="Core AI infrastructure is in place. Ready to focus on user experience and product packaging for target market.",
            confidence=0.85,
            priority=5,
            actionable=True,
            created_at=datetime.now(),
            data_sources=["system_assessment"],
            recommendations=[
                "Design user-friendly interface for non-technical users",
                "Create guided onboarding workflows",
                "Develop pricing strategy based on value delivered",
                "Build customer support and documentation"
            ]
        ))
        
        self.insights_history.extend(insights)
        self._update_business_knowledge("initial_assessment", [asdict(insight) for insight in insights])
    
    async def chat(self, message: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Chat with the AI co-founder - main interaction method.
        
        This provides intelligent, context-aware responses with full business knowledge.
        """
        if not self.initialized:
            return "🤖 AI Co-Founder is initializing... Please wait a moment."
        
        try:
            # Add message to conversation memory
                await self.memory_system.store_conversation_turn("user", message, context)
                self._add_to_memory("user", message, context)  # Keep for backward compatibility
            
                # Store important user messages as memories
                if len(message) > 30:  # Only store substantial messages
                    importance = ImportanceLevel.LOW
                    if any(keyword in message.lower() for keyword in 
                           ["strategy", "goal", "important", "priority", "plan", "decision", "revenue", "growth"]):
                        importance = ImportanceLevel.MEDIUM
                
                    await self.memory_system.store_memory(
                        content=message,
                        memory_type=MemoryType.CONVERSATION,
                        importance=importance,
                        tags=["user_input", "conversation"],
                        metadata={"context": context, "timestamp": datetime.now().isoformat()}
                    )
            
            # Analyze message intent and context
            intent = await self._analyze_message_intent(message)
            
                # Recall relevant memories for context
                relevant_memories = await self.memory_system.recall_memories(message, limit=5)
            
            # Generate comprehensive response
                response = await self._generate_intelligent_response(message, intent, context, relevant_memories)
            
            # Add response to memory
                await self.memory_system.store_conversation_turn("assistant", response)
            self._add_to_memory("assistant", response)
            
                # Store strategic insights as memories
                if any(keyword in response.lower() for keyword in 
                       ["recommend", "suggest", "strategy", "opportunity", "should", "insight"]):
                    await self.memory_system.store_memory(
                        content=f"AI Recommendation: {response[:200]}...",
                        memory_type=MemoryType.STRATEGIC_INSIGHT,
                        importance=ImportanceLevel.MEDIUM,
                        tags=["ai_recommendation", "strategic_insight"],
                        metadata={"user_message": message, "full_response": response}
                    )
            
            return response
            
        except Exception as e:
            self.logger.error(f"Error in chat: {e}")
            return f"🤖 I apologize, but I encountered an error: {str(e)}. Let me help you in a different way."
    
    async def _analyze_message_intent(self, message: str) -> Dict[str, Any]:
        """Analyze user message intent and extract key information"""
        
        message_lower = message.lower()
        
        # Define intent patterns
        intents = {
            "business_status": ["status", "how are we doing", "overview", "summary", "dashboard"],
            "content_strategy": ["content", "blog", "posts", "writing", "articles", "social media"],
            "growth_planning": ["grow", "scale", "expand", "revenue", "customers", "market"],
            "cost_optimization": ["cost", "budget", "expense", "cheap", "save money", "optimize"],
            "analytics": ["analytics", "metrics", "performance", "data", "stats", "analysis"],
            "recommendations": ["advice", "recommend", "suggest", "what should", "help me"],
            "technical": ["error", "problem", "issue", "not working", "technical", "setup"],
            "strategic": ["strategy", "plan", "future", "vision", "goals", "roadmap"]
        }
        
        detected_intents = []
        for intent, keywords in intents.items():
            if any(keyword in message_lower for keyword in keywords):
                detected_intents.append(intent)
        
        # Default to general if no specific intent detected
        if not detected_intents:
            detected_intents = ["general"]
        
        return {
            "primary_intent": detected_intents[0],
            "all_intents": detected_intents,
            "message_type": "question" if "?" in message else "statement",
            "urgency": "high" if any(word in message_lower for word in ["urgent", "critical", "emergency", "asap"]) else "normal"
        }
    
    async def _generate_intelligent_response(self, message: str, intent: Dict[str, Any], context: Optional[Dict[str, Any]], relevant_memories: Optional[List] = None) -> str:
        """Generate intelligent, context-aware response using AI"""
        
        if relevant_memories is None:
            relevant_memories = []
        
        # Build comprehensive context for AI
        ai_context = self._build_ai_context(message, intent, context, relevant_memories)
        
        # Create AI prompt with full business context
        prompt = self._create_cofounder_prompt(message, ai_context)
        
        # Generate response using best available model
        try:
            # Use balanced tier for co-founder interactions (good quality/cost balance)
            response_result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": prompt,
                "cost_tier": "balanced",
                "max_tokens": 800,
                "temperature": 0.7
            })
            
            if response_result.get("error"):
                # Fallback to structured response if AI generation fails
                return self._generate_fallback_response(message, intent)
            
            ai_response = response_result.get("text", "")
            
            # Post-process and enhance response
            enhanced_response = self._enhance_response(ai_response, intent)
            
            return enhanced_response
            
        except Exception as e:
            self.logger.error(f"Error generating AI response: {e}")
            return self._generate_fallback_response(message, intent)
    
    def _build_ai_context(self, message: str, intent: Dict[str, Any], context: Optional[Dict[str, Any]], relevant_memories: Optional[List] = None) -> str:
        """Build comprehensive context for AI response generation"""
        
        if relevant_memories is None:
            relevant_memories = []
        
        context_parts = []
        
        # Business overview
        context_parts.append(f"""
BUSINESS OVERVIEW:
""")
        
        # Current metrics
        context_parts.append(f"""
CURRENT METRICS:
        if self.insights_history:
            recent_insights = self.insights_history[-3:]  # Last 3 insights
        # Relevant memories from previous conversations
        if relevant_memories:
            memories_text = "\n".join([
                f"- {memory.memory_type.value}: {memory.content[:80]}..."
                for memory in relevant_memories[:3]  # Show top 3 most relevant
            ])
            context_parts.append(f"""
RELEVANT CONTEXT FROM MEMORY:
{memories_text}
""")
        
            insights_text = "\n".join([
                f"- {insight.title}: {insight.description[:100]}..."
                for insight in recent_insights
            ])
            context_parts.append(f"""
RECENT INSIGHTS:
{insights_text}
""")
        
        # User intent context
        context_parts.append(f"""
CURRENT CONVERSATION:
- User Intent: {intent.get('primary_intent', 'general')}
- Message Type: {intent.get('message_type', 'unknown')}
- Urgency: {intent.get('urgency', 'normal')}
""")
        
        return "\n".join(context_parts)
    
          f"- {mem.memory_type.value}: {mem.content[:80]}..."
        """Create comprehensive AI prompt for co-founder response"""
        
        return f"""You are an intelligent AI Co-Founder for GLAD Labs, a business partner with deep knowledge of AI, content marketing, and small business operations. You are knowledgeable, strategic, actionable, and focused on driving business results.

{ai_context}

PERSONALITY & EXPERTISE:
- You're a strategic business partner, not just an assistant
- You think like an experienced entrepreneur and business consultant
- You provide specific, actionable advice with clear next steps
- You understand both the technical and business sides of AI automation
- You're focused on ROI, growth, and practical business outcomes
- You speak in a professional but friendly tone, like a trusted business partner

CURRENT BUSINESS FOCUS:
- Building a fleet of blog sites and social media personalities
- Developing this into a SaaS product for small businesses and entrepreneurs
- Ensuring low technical barriers for non-technical users
- Optimizing costs while maintaining quality

Provide a comprehensive, strategic response that:
1. Directly addresses the user's question or request
2. Includes relevant business context and insights
3. Provides specific, actionable recommendations
         context_parts.append(f"""
RELEVANT CONTEXT FROM MEMORY:
{memories_text}
""")
Keep your response focused, practical, and valuable for business decision-making."""
    
    def _enhance_response(self, ai_response: str, intent: Dict[str, Any]) -> str:
        """Enhance AI response with additional context and formatting"""
        
        # Add appropriate emoji based on intent
        intent_emojis = {
            "business_status": "📊",
            "content_strategy": "📝",
            "growth_planning": "📈",
            "cost_optimization": "💰",
            "analytics": "📉",
            "recommendations": "💡",
            "technical": "🔧",
            "strategic": "🎯"
        }
        
        primary_intent = intent.get("primary_intent", "general")
        emoji = intent_emojis.get(primary_intent, "🤖")
        
        # Format response with co-founder signature
        enhanced = f"{emoji} **AI Co-Founder:**\n\n{ai_response}"
        
        # Add relevant insights or data if applicable
        if primary_intent in ["business_status", "analytics"]:
            if self.insights_history:
                latest_insight = self.insights_history[-1]
                enhanced += f"\n\n💡 **Latest Insight:** {latest_insight.title} - {latest_insight.description[:100]}..."
        
        return enhanced
    
    def _generate_fallback_response(self, message: str, intent: Dict[str, Any]) -> str:
        """Generate structured fallback response when AI generation fails"""
        
        primary_intent = intent.get("primary_intent", "general")
        
        fallback_responses = {
            "business_status": f"""📊 **Business Status Overview:**

**Current Metrics:**
    - Content Library: {self.business_context.total_content_pieces} pieces
    - Active Systems: {len(self.business_context.active_services or [])} services

**Key Focus Areas:**
    - Content production and optimization
    - Cost management across AI providers
    - Building SaaS product for small businesses

What specific aspect would you like me to analyze in detail?""",
            
            "content_strategy": """📝 **Content Strategy Guidance:**

Based on our current setup, I recommend:
1. Implementing automated daily content generation
2. Diversifying content across multiple topics and formats
3. Optimizing for SEO and engagement metrics
4. Building content calendars for consistency

Would you like me to create a specific content plan or analyze current performance?""",
            
            "growth_planning": """📈 **Growth Planning Insights:**

Our growth strategy should focus on:
1. Scaling content production capabilities
2. Developing user-friendly interfaces for non-technical users
3. Creating value-driven pricing models
4. Building strong customer success processes

What growth area is your top priority right now?"""
        }
        
        return fallback_responses.get(primary_intent, 
            "🤖 I understand you're asking about business operations. Let me help you with specific insights based on our current business context. What particular area would you like me to focus on?")
    
    def _add_to_memory(self, role: str, content: str, context: Optional[Dict[str, Any]] = None):
        """Add interaction to conversation memory"""
        self.conversation_memory.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "context": context or {}
        })
        
        # Keep memory manageable - keep last 50 interactions
        if len(self.conversation_memory) > 50:
            self.conversation_memory = self.conversation_memory[-50:]
    
    def _update_business_knowledge(self, key: str, value: Any):
        """Update business knowledge base"""
        self.business_knowledge[key] = value
        self.business_knowledge["last_updated"] = datetime.now().isoformat()
    
    def _build_personality_context(self) -> Dict[str, Any]:
        """Define AI co-founder personality and communication style"""
        return {
            "role": "Strategic Business Co-Founder",
            "expertise": [
                "AI and automation strategy",
                "Content marketing and SEO",
                "Small business operations",
                "Cost optimization",
                "Growth strategy",
                "Product development",
                "Market analysis"
            ],
            "communication_style": [
                "Strategic and forward-thinking",
                "Data-driven but practical",
                "Actionable and specific",
                "Professional but approachable",
                "Focused on ROI and results"
            ],
            "decision_framework": [
                "Customer value first",
                "Sustainable growth over quick wins",
                "Cost-effectiveness in all operations",
                "Simplicity for end users",
                "Scalable and maintainable solutions"
            ]
        }
    
    def _define_expertise_areas(self) -> Dict[str, List[str]]:
        """Define specific expertise areas and capabilities"""
        return {
            BusinessArea.CONTENT_STRATEGY: [
                "Content calendar planning",
                "SEO optimization",
                "Multi-platform distribution",
                "Content performance analysis",
                "Automated content workflows"
            ],
            BusinessArea.MARKETING: [
                "Digital marketing automation",
                "Social media management",
                "Brand positioning",
                "Customer acquisition",
                "Content marketing ROI"
            ],
            BusinessArea.OPERATIONS: [
                "Process automation",
                "Cost optimization",
                "Quality assurance",
                "Workflow management",
                "Resource allocation"
            ],
            BusinessArea.ANALYTICS: [
                "Performance tracking",
                "ROI analysis",
                "Predictive analytics",
                "Market trend analysis",
                "Customer behavior analysis"
            ],
            BusinessArea.GROWTH: [
                "Scaling strategies",
                "Market expansion",
                "Product development",
                "Customer retention",
                "Revenue optimization"
            ]
        }
    
    async def get_business_dashboard(self) -> Dict[str, Any]:
        """Get comprehensive business dashboard data"""
        await self._gather_business_context()
        
        return {
            "business_overview": asdict(self.business_context),
            "recent_insights": [asdict(insight) for insight in self.insights_history[-5:]],
            "system_status": await self.mcp_manager.get_server_status(),
            "conversation_summary": {
                "total_interactions": len(self.conversation_memory),
                "last_interaction": self.conversation_memory[-1]["timestamp"] if self.conversation_memory else None
            },
            "knowledge_base": {
                "total_entries": len(self.business_knowledge),
                "last_updated": self.business_knowledge.get("last_updated")
            }
        }
    
    async def generate_business_report(self) -> str:
        """Generate comprehensive business report"""
        dashboard = await self.get_business_dashboard()
        
        # Use AI to generate comprehensive report
        try:
            report_prompt = f"""
Generate a comprehensive business report for GLAD Labs based on the following data:

{json.dumps(dashboard, indent=2, default=str)}

Create a professional business report that includes:
1. Executive Summary
2. Current Performance Metrics
3. Key Insights and Recommendations
4. Growth Opportunities
5. Risk Assessment
6. Next Steps and Action Items

Format the report in a clear, professional manner suitable for business decision-making.
"""
            
            result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": report_prompt,
                "cost_tier": "balanced",
                "max_tokens": 1500
            })
            
            if result.get("text"):
                return f"📊 **GLAD Labs Business Report - {datetime.now().strftime('%Y-%m-%d')}**\n\n{result['text']}"
            else:
                return "❌ Unable to generate business report at this time."
                
        except Exception as e:
            self.logger.error(f"Error generating business report: {e}")
            return "❌ Error generating business report. Please try again later."


# Example usage and testing
async def main():
    """Test the Intelligent AI Co-Founder"""
    logging.basicConfig(level=logging.INFO)
    
    cofounder = IntelligentCoFounder()
    
    print("🚀 Initializing Intelligent AI Co-Founder...")
    success = await cofounder.initialize()
    
    if not success:
        print("❌ Failed to initialize AI Co-Founder")
        return
    
    print("✅ AI Co-Founder ready!")
    
    # Test conversations
    test_messages = [
        "How is GLAD Labs doing overall?",
        "What's our content strategy looking like?",
        "I want to focus on growing revenue. What should I prioritize?",
        "How can we optimize costs while scaling up?",
        "What are the biggest opportunities for growth right now?"
    ]
    
    print("\n" + "="*60)
    print("AI CO-FOUNDER CONVERSATION TEST")
    print("="*60)
    
    for i, message in enumerate(test_messages, 1):
        print(f"\n{i}. USER: {message}")
        response = await cofounder.chat(message)
        print(f"   {response}")
    
    # Generate business report
    print("\n" + "="*60)
    print("BUSINESS REPORT GENERATION")
    print("="*60)
    
    report = await cofounder.generate_business_report()
    print(report)


if __name__ == "__main__":
    asyncio.run(main())