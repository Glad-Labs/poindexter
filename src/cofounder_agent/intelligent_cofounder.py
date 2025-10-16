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

try:
    from mcp.client_manager import MCPClientManager
    from mcp.servers.ai_model_server import AIModelServer
    from mcp.servers.strapi_server import StrapiMCPServer
except ImportError:
    # Fallback if MCP not available
    MCPClientManager = None
    AIModelServer = None
    StrapiMCPServer = None

try:
    from .business_intelligence import BusinessIntelligenceSystem
    from .memory_system import AIMemorySystem, MemoryType, ImportanceLevel
    from .notification_system import SmartNotificationSystem
    from .advanced_dashboard import AdvancedBusinessDashboard
    from .multi_agent_orchestrator import MultiAgentOrchestrator, TaskPriority
    from .voice_interface import VoiceInterfaceSystem
except ImportError:
    # For direct execution, use absolute imports
    from business_intelligence import BusinessIntelligenceSystem
    from memory_system import AIMemorySystem, MemoryType, ImportanceLevel
    from notification_system import SmartNotificationSystem
    from advanced_dashboard import AdvancedBusinessDashboard
    from multi_agent_orchestrator import MultiAgentOrchestrator, TaskPriority
    from voice_interface import VoiceInterfaceSystem


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
        self.mcp_manager = MCPClientManager() if MCPClientManager else None
        self.business_context = BusinessContext(business_name=business_name)
        
        # Enhanced intelligence systems
        self.business_intelligence = BusinessIntelligenceSystem()
        self.memory_system = AIMemorySystem()
        
        # Advanced systems
        self.notification_system = SmartNotificationSystem()
        self.dashboard = AdvancedBusinessDashboard()
        self.orchestrator = MultiAgentOrchestrator()
        self.voice_interface = VoiceInterfaceSystem()
        
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
        
        # Initialize advanced systems
        asyncio.create_task(self._initialize_advanced_systems())
    
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
    
    async def _initialize_advanced_systems(self):
        """Initialize advanced AI systems"""
        try:
            # Initialize notification system
            if hasattr(self, 'notification_system'):
                await self.notification_system.initialize()
            
            # Start orchestration system
            if hasattr(self, 'orchestrator'):
                # Orchestrator starts automatically
                pass
            
            # Set up dashboard monitoring
            if hasattr(self, 'dashboard'):
                # Dashboard starts collecting metrics automatically
                pass
            
            self.logger.info("Advanced systems initialized successfully")
        except Exception as e:
            self.logger.error(f"Error initializing advanced systems: {e}")
    
    async def _setup_mcp_systems(self):
        """Set up MCP servers and capabilities"""
        if not self.mcp_manager:
            return
            
        # Register AI model server
        if AIModelServer:
            ai_server = AIModelServer()
            await self.mcp_manager.register_server("ai-models", ai_server)
        
        # Register Strapi CMS server
        if StrapiMCPServer:
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
        context_parts.append("BUSINESS OVERVIEW:")
        
        # Current metrics
        context_parts.append("CURRENT METRICS:")
        
        # Relevant insights
        if self.insights_history:
            recent_insights = self.insights_history[-3:]  # Last 3 insights
            insights_text = "\n".join([
                f"- {insight.title}: {insight.description[:100]}..."
                for insight in recent_insights
            ])
            context_parts.append(f"RECENT INSIGHTS:\n{insights_text}")
        
        # Relevant memories from previous conversations
        if relevant_memories:
            memories_text = "\n".join([
                f"- {memory.memory_type.value}: {memory.content[:80]}..."
                for memory in relevant_memories[:3]  # Show top 3 most relevant
            ])
            context_parts.append(f"RELEVANT CONTEXT FROM MEMORY:\n{memories_text}")
        
        # User intent context
        context_parts.append(f"CURRENT CONVERSATION:\n- User Intent: {intent.get('primary_intent', 'general')}\n- Message Type: {intent.get('message_type', 'unknown')}\n- Urgency: {intent.get('urgency', 'normal')}")
        
        return "\n\n".join(context_parts)
    
    def _create_cofounder_prompt(self, message: str, ai_context: str) -> str:
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

USER MESSAGE: {message}

Provide a comprehensive, strategic response that:
1. Directly addresses the user's question or request
2. Includes relevant business context and insights
3. Provides specific, actionable recommendations
4. Shows deep understanding of the business

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
        
        # Create fallback response based on intent
        if primary_intent == "business_status":
            return f"""Business Status Overview:

Current Metrics:
    - Content Library: {self.business_context.total_content_pieces} pieces
    - Active Systems: {len(self.business_context.active_services or [])} services

Key Focus Areas:
    - Content production and optimization
    - Cost management across AI providers
    - Building SaaS product for small businesses

What specific aspect would you like me to analyze in detail?"""
        
        elif primary_intent == "content_strategy":
            return """Content Strategy Guidance:

Based on our current setup, I recommend:
1. Implementing automated daily content generation
2. Diversifying content across multiple topics and formats
3. Optimizing for SEO and engagement metrics
4. Building content calendars for consistency

Would you like me to create a specific content plan or analyze current performance?"""
        
        elif primary_intent == "growth_planning":
            return """Growth Planning Insights:

Our growth strategy should focus on:
1. Scaling content production capabilities
2. Developing user-friendly interfaces for non-technical users
3. Creating value-driven pricing models
4. Building strong customer success processes

What growth area is your top priority right now?"""
        
        else:
            return f"I'm your AI Co-Founder assistant. I understand you asked about: {message[:50]}{'...' if len(message) > 50 else ''}\n\nI'm here to help with business strategy, content planning, growth optimization, and technical guidance for GLAD Labs.\n\nHow can I assist you with your business needs today?"
    
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
            report_prompt = f"Generate a comprehensive business report for GLAD Labs based on the following data:\n\n{json.dumps(dashboard, indent=2, default=str)}\n\nCreate a professional business report that includes:\n1. Executive Summary\n2. Current Performance Metrics\n3. Key Insights and Recommendations\n4. Growth Opportunities\n5. Risk Assessment\n6. Next Steps and Action Items\n\nFormat the report in a clear, professional manner suitable for business decision-making."
            
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
    
    async def create_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new task with AI assistance"""
        try:
            # Enhance task data with AI insights
            enhanced_task = await self._enhance_task_with_ai(task_data)
            
            # Store task creation in memory
            await self.memory_system.store_memory(
                content=f"Created task: {enhanced_task.get('topic')}",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.MEDIUM,
                tags=["task_creation", "management"],
                metadata={"task_data": enhanced_task}
            )
            
            self.logger.info(f"Task created with AI enhancement: {enhanced_task.get('topic')}")
            return enhanced_task
            
        except Exception as e:
            self.logger.error(f"Error creating task: {e}")
            return {"error": str(e)}
    
    async def _enhance_task_with_ai(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Enhance task data with AI insights and optimization"""
        try:
            enhancement_prompt = f"""
Analyze and enhance this task for optimal execution:

Task Data: {json.dumps(task_data, indent=2)}

Provide enhancements including:
1. Optimized keywords for better SEO
2. Refined target audience segmentation  
3. Suggested content angles and approaches
4. Priority level recommendation
5. Estimated completion timeline
6. Success metrics and KPIs

Return as JSON with enhanced fields.
"""
            
            result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": enhancement_prompt,
                "cost_tier": "balanced",
                "max_tokens": 800
            })
            
            if result.get("text"):
                # Try to parse AI enhancement
                try:
                    enhanced_data = json.loads(result["text"])
                    return {**task_data, **enhanced_data}
                except json.JSONDecodeError:
                    # If not valid JSON, add AI suggestions as text
                    task_data["ai_enhancement"] = result["text"]
                    
            return task_data
            
        except Exception as e:
            self.logger.error(f"Error enhancing task with AI: {e}")
            return task_data
    
    async def analyze_command_intent(self, command: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Analyze command intent using AI and business context"""
        try:
            intent_prompt = f"""
Analyze this business command for intent and required actions:

Command: "{command}"
Context: {json.dumps(context or {}, indent=2)}

Business Context:
- Company: GLAD Labs (AI content and business automation)
- Focus: Content marketing, task management, business intelligence
- Goal: Scaling through AI automation

Provide analysis as JSON:
{{
    "intent": "primary_intent_category",
    "confidence": 0.0-1.0,
    "entities": ["extracted", "entities"], 
    "required_actions": [
        {{"type": "action_type", "priority": "high/medium/low", "data": {{}}}}
    ],
    "business_impact": "description of potential impact",
    "recommendations": ["actionable recommendations"]
}}
"""
            
            result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": intent_prompt,
                "cost_tier": "fast",
                "max_tokens": 500
            })
            
            if result.get("text"):
                try:
                    return json.loads(result["text"])
                except json.JSONDecodeError:
                    pass
            
            # Fallback to rule-based analysis
            return await self._fallback_intent_analysis(command, context)
            
        except Exception as e:
            self.logger.error(f"Error analyzing command intent: {e}")
            return await self._fallback_intent_analysis(command, context)
    
    async def _fallback_intent_analysis(self, command: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Fallback intent analysis using rules"""
        command_lower = command.lower()
        
        # Task management intents
        if any(word in command_lower for word in ["create", "new", "add", "make"]) and "task" in command_lower:
            return {
                "intent": "create_task",
                "confidence": 0.8,
                "entities": ["task"],
                "required_actions": [{"type": "create_task", "priority": "medium", "data": {}}],
                "business_impact": "New task creation will increase workload but advance business goals",
                "recommendations": ["Ensure task aligns with strategic priorities", "Set clear success metrics"]
            }
        
        # Business analysis intents
        elif any(word in command_lower for word in ["report", "metrics", "performance", "analytics"]):
            return {
                "intent": "business_analysis",
                "confidence": 0.9,
                "entities": ["business", "metrics"],
                "required_actions": [{"type": "generate_report", "priority": "high", "data": {}}],
                "business_impact": "Business analysis provides insights for strategic decision-making",
                "recommendations": ["Review all key metrics", "Identify improvement opportunities"]
            }
        
        # Default general intent
        return {
            "intent": "general_inquiry",
            "confidence": 0.5,
            "entities": [],
            "required_actions": [],
            "business_impact": "General inquiry requiring contextual response",
            "recommendations": ["Provide helpful information", "Offer specific assistance options"]
        }
    
    async def create_strategic_plan(self, goals: List[str], timeframe: str = "quarterly") -> Dict[str, Any]:
        """Create a strategic business plan with AI assistance"""
        try:
            current_context = await self.get_business_dashboard()
            
            planning_prompt = f"""
Create a strategic business plan for GLAD Labs:

Goals: {json.dumps(goals)}
Timeframe: {timeframe}
Current Business Context: {json.dumps(current_context, default=str)}

Create a comprehensive strategic plan including:
1. Executive Summary
2. Current State Analysis  
3. Strategic Objectives and Key Results (OKRs)
4. Action Plan with Timeline
5. Resource Requirements
6. Risk Assessment and Mitigation
7. Success Metrics and KPIs
8. Review and Adjustment Framework

Format as detailed business document.
"""
            
            result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": planning_prompt,
                "cost_tier": "premium",
                "max_tokens": 2000
            })
            
            plan = {
                "id": f"plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "goals": goals,
                "timeframe": timeframe,
                "created_at": datetime.now().isoformat(),
                "plan_content": result.get("text", "Unable to generate plan at this time"),
                "status": "draft"
            }
            
            # Store strategic plan in memory
            await self.memory_system.store_memory(
                content=f"Strategic plan created with {len(goals)} goals for {timeframe}",
                memory_type=MemoryType.STRATEGIC_INSIGHT,
                importance=ImportanceLevel.HIGH,
                tags=["strategic_planning", "business_strategy"],
                metadata={"plan_id": plan["id"], "goals": goals}
            )
            
            return plan
            
        except Exception as e:
            self.logger.error(f"Error creating strategic plan: {e}")
            return {"error": str(e)}
    
    async def optimize_content_strategy(self, current_performance: Dict[str, Any]) -> Dict[str, Any]:
        """Optimize content strategy based on performance data"""
        try:
            optimization_prompt = f"""
Optimize GLAD Labs content strategy based on performance data:

Current Performance: {json.dumps(current_performance, default=str)}
Business Context: AI content marketing and automation company

Provide optimization recommendations including:
1. Content topic prioritization
2. Publishing schedule optimization
3. Audience targeting refinements
4. Content format recommendations
5. SEO optimization strategies
6. Performance improvement tactics
7. Resource allocation suggestions

Format as actionable strategy document.
"""
            
            result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": optimization_prompt,
                "cost_tier": "balanced",
                "max_tokens": 1200
            })
            
            optimization = {
                "id": f"content-opt-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "analysis_date": datetime.now().isoformat(),
                "performance_data": current_performance,
                "recommendations": result.get("text", "Unable to generate recommendations"),
                "status": "active"
            }
            
            # Store optimization in memory
            await self.memory_system.store_memory(
                content="Content strategy optimization completed",
                memory_type=MemoryType.STRATEGIC_INSIGHT,
                importance=ImportanceLevel.HIGH,
                tags=["content_strategy", "optimization"],
                metadata={"optimization_id": optimization["id"]}
            )
            
            return optimization
            
        except Exception as e:
            self.logger.error(f"Error optimizing content strategy: {e}")
            return {"error": str(e)}
    
    async def delegate_task_to_agent(self, task_description: str, requirements: List[str], 
                                   priority: str = "medium") -> Dict[str, Any]:
        """Delegate a task to the multi-agent orchestrator"""
        try:
            # Convert priority string to enum
            priority_map = {
                "low": TaskPriority.LOW,
                "medium": TaskPriority.MEDIUM,
                "high": TaskPriority.HIGH,
                "critical": TaskPriority.CRITICAL
            }
            
            task_priority = priority_map.get(priority.lower(), TaskPriority.MEDIUM)
            
            # Create orchestration task
            task_id = await self.orchestrator.create_task(
                name=f"Delegated Task: {task_description[:50]}...",
                description=task_description,
                requirements=requirements,
                priority=task_priority
            )
            
            # Store in memory
            await self.memory_system.store_memory(
                content=f"Delegated task to agents: {task_description}",
                memory_type=MemoryType.BUSINESS_FACT,
                importance=ImportanceLevel.MEDIUM,
                tags=["task_delegation", "agent_orchestration"],
                metadata={"task_id": task_id, "requirements": requirements}
            )
            
            return {
                "success": True,
                "task_id": task_id,
                "message": f"Task delegated successfully. Tracking ID: {task_id}"
            }
            
        except Exception as e:
            self.logger.error(f"Error delegating task: {e}")
            return {"error": str(e)}
    
    async def get_comprehensive_status(self) -> Dict[str, Any]:
        """Get comprehensive status from all systems"""
        try:
            # Get dashboard data
            dashboard_data = await self.dashboard.get_dashboard_data()
            
            # Get orchestration status
            orchestration_status = await self.orchestrator.get_orchestration_status()
            
            # Get agent recommendations
            agent_recommendations = await self.orchestrator.get_agent_recommendations()
            
            # Get recent notifications
            recent_notifications = await self.notification_system.get_recent_notifications(limit=10)
            
            # Get business context
            business_context = await self.get_business_context()
            
            return {
                "timestamp": datetime.now().isoformat(),
                "business_health": dashboard_data.get("metrics", {}).get("summary", {}),
                "dashboard": dashboard_data,
                "orchestration": orchestration_status,
                "recommendations": agent_recommendations,
                "notifications": recent_notifications,
                "business_context": business_context,
                "system_status": "operational"
            }
            
        except Exception as e:
            self.logger.error(f"Error getting comprehensive status: {e}")
            return {"error": str(e)}
    
    async def create_strategic_workflow(self, workflow_name: str, objectives: List[str]) -> Dict[str, Any]:
        """Create a comprehensive strategic workflow"""
        try:
            # Define workflow steps based on objectives
            workflow_steps = []
            
            for i, objective in enumerate(objectives):
                if "content" in objective.lower():
                    workflow_steps.append({
                        "name": f"Content Strategy for: {objective}",
                        "description": f"Develop content strategy to achieve: {objective}",
                        "requirements": ["blog_writing", "content_optimization"],
                        "priority": 3 if i == 0 else 2,
                        "input_data": {"objective": objective}
                    })
                
                elif "market" in objective.lower() or "research" in objective.lower():
                    workflow_steps.append({
                        "name": f"Market Research for: {objective}",
                        "description": f"Conduct market research to support: {objective}",
                        "requirements": ["market_analysis", "competitor_analysis"],
                        "priority": 3,
                        "input_data": {"objective": objective}
                    })
                
                elif "analysis" in objective.lower() or "data" in objective.lower():
                    workflow_steps.append({
                        "name": f"Data Analysis for: {objective}",
                        "description": f"Perform data analysis to achieve: {objective}",
                        "requirements": ["business_intelligence", "data_visualization"],
                        "priority": 2,
                        "input_data": {"objective": objective}
                    })
                
                elif "plan" in objective.lower() or "strategy" in objective.lower():
                    workflow_steps.append({
                        "name": f"Strategic Planning for: {objective}",
                        "description": f"Develop strategic plan for: {objective}",
                        "requirements": ["business_planning", "process_optimization"],
                        "priority": 3,
                        "input_data": {"objective": objective}
                    })
            
            # Create workflow in orchestrator
            workflow_id = await self.orchestrator.create_workflow(workflow_name, workflow_steps)
            
            # Store strategic plan in memory
            await self.memory_system.store_memory(
                content=f"Created strategic workflow: {workflow_name}",
                memory_type=MemoryType.STRATEGIC_INSIGHT,
                importance=ImportanceLevel.HIGH,
                tags=["strategic_planning", "workflow", "business_objectives"],
                metadata={
                    "workflow_id": workflow_id,
                    "objectives": objectives,
                    "steps_count": len(workflow_steps)
                }
            )
            
            return {
                "success": True,
                "workflow_id": workflow_id,
                "steps_created": len(workflow_steps),
                "message": f"Strategic workflow '{workflow_name}' created with {len(workflow_steps)} steps"
            }
            
        except Exception as e:
            self.logger.error(f"Error creating strategic workflow: {e}")
            return {"error": str(e)}


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