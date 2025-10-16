"""
MCP Integration for Co-Founder Agent

Enhances the existing co-founder agent with MCP capabilities for flexible
AI model selection and standardized tool access.
"""

import asyncio
import logging
import sys
import os
from typing import Dict, Any, Optional

# Add MCP module to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

try:
    from mcp.mcp_orchestrator import MCPContentOrchestrator
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logging.warning("MCP orchestrator not available")


class MCPEnhancedCoFounder:
    """
    MCP-enhanced version of the co-founder agent.
    
    Provides:
    1. MCP-based content creation with flexible model selection
    2. Cost-optimized AI operations
    3. Standardized tool access across services
    4. Enhanced capability discovery
    """
    
    def __init__(self, original_orchestrator: Any):
        self.original_orchestrator = original_orchestrator
        self.logger = logging.getLogger("cofounder.mcp")
        
        # Initialize MCP orchestrator if available
        if MCP_AVAILABLE:
            self.mcp_orchestrator = MCPContentOrchestrator()
            self.mcp_enabled = False  # Will be set to True after initialization
        else:
            self.mcp_orchestrator = None
            self.mcp_enabled = False
    
    async def initialize_mcp(self) -> bool:
        """Initialize MCP capabilities"""
        if not self.mcp_orchestrator:
            return False
        
        try:
            success = await self.mcp_orchestrator.initialize()
            if success:
                self.mcp_enabled = True
                self.logger.info("MCP capabilities initialized successfully")
                return True
            else:
                self.logger.warning("MCP initialization failed, falling back to original methods")
                return False
        except Exception as e:
            self.logger.error(f"Error initializing MCP: {e}")
            return False
    
    async def process_command_enhanced(self, command: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Enhanced command processing with MCP capabilities.
        
        Tries MCP methods first, falls back to original implementation.
        """
        try:
            # Handle MCP-specific commands
            if self.mcp_enabled:
                mcp_result = await self._try_mcp_command(command, context)
                if mcp_result:
                    return mcp_result
            
            # Fallback to original orchestrator
            if hasattr(self.original_orchestrator, 'process_command'):
                return await self.original_orchestrator.process_command(command, context or {})
            else:
                return self.original_orchestrator.process_command(command)
                
        except Exception as e:
            self.logger.error(f"Error in enhanced command processing: {e}")
            return f"❌ Error processing command: {str(e)}"
    
    async def _try_mcp_command(self, command: str, context: Optional[Dict[str, Any]]) -> Optional[str]:
        """Try to handle command using MCP capabilities"""
        
        command_lower = command.lower()
        
        # Content creation commands
        if any(keyword in command_lower for keyword in ["create content", "write article", "blog post", "generate content"]):
            return await self._handle_mcp_content_creation(command, context)
        
        # Model information commands
        elif any(keyword in command_lower for keyword in ["available models", "ai models", "model costs"]):
            return await self._handle_model_info()
        
        # Cost optimization commands
        elif any(keyword in command_lower for keyword in ["cheap", "cost", "budget", "optimize"]):
            return await self._handle_cost_optimized_content(command, context)
        
        # Status commands
        elif any(keyword in command_lower for keyword in ["mcp status", "capabilities", "tools available"]):
            return await self._handle_mcp_status()
        
        return None  # Let original orchestrator handle it
    
    async def _handle_mcp_content_creation(self, command: str, context: Optional[Dict[str, Any]]) -> str:
        """Handle content creation using MCP"""
        try:
            # Extract topic from command
            topic = self._extract_topic(command)
            if not topic:
                return "❓ Please specify a topic for content creation. Example: 'Create content about AI in healthcare'"
            
            # Determine cost tier based on context or command
            cost_tier = "balanced"  # Default
            if context and "cost_tier" in context:
                cost_tier = context["cost_tier"]
            elif any(word in command.lower() for word in ["cheap", "budget"]):
                cost_tier = "cheap"
            elif any(word in command.lower() for word in ["premium", "best"]):
                cost_tier = "premium"
            
            self.logger.info(f"Creating content about '{topic}' using {cost_tier} tier")
            
            # Use MCP orchestrator for content creation
            result = await self.mcp_orchestrator.research_and_create_post(
                topic=topic,
                target_audience=context.get("target_audience", "general") if context else "general",
                category=context.get("category", "") if context else "",
                tags=context.get("tags", []) if context else []
            )
            
            if result.get("success"):
                post_id = result.get("final_post_id")
                total_cost = result.get("total_cost", 0.0)
                return f"""✅ Content creation completed successfully!

📝 **Topic:** {topic}
🆔 **Post ID:** {post_id}
💰 **Total Cost:** ${total_cost:.4f}
🎯 **Cost Tier:** {cost_tier}

**Workflow Stages Completed:**
• Research and planning
• Content generation  
• Quality assurance review
• Publishing to CMS

The content is now live and ready for your audience!"""
            else:
                error = result.get("error", "Unknown error")
                return f"❌ Content creation failed: {error}"
                
        except Exception as e:
            self.logger.error(f"Error in MCP content creation: {e}")
            return f"❌ Error in content creation: {str(e)}"
    
    async def _handle_model_info(self) -> str:
        """Handle model information requests"""
        try:
            models_info = await self.mcp_orchestrator.get_available_models()
            
            if models_info.get("error"):
                return f"❌ Error getting model info: {models_info['error']}"
            
            providers = models_info.get("providers_available", {})
            available_models = models_info.get("available_models", {})
            
            response = "🤖 **Available AI Models:**\n\n"
            
            # Provider status
            response += "**Providers Online:**\n"
            for provider, available in providers.items():
                status = "✅" if available else "❌"
                response += f"{status} {provider.title()}\n"
            
            response += "\n**Models by Cost Tier:**\n"
            for tier, models in available_models.items():
                if models:
                    response += f"• **{tier.title()}:** {len(models)} models\n"
                    for model in models[:2]:  # Show first 2 models per tier
                        cost = f"${model['cost_per_1k_tokens']:.3f}/1k" if model['cost_per_1k_tokens'] > 0 else "Free"
                        response += f"  - {model['name']} ({model['provider']}) - {cost}\n"
            
            total_models = sum(len(models) for models in available_models.values())
            response += f"\n**Total Available:** {total_models} models"
            
            return response
            
        except Exception as e:
            return f"❌ Error getting model information: {str(e)}"
    
    async def _handle_cost_optimized_content(self, command: str, context: Optional[Dict[str, Any]]) -> str:
        """Handle cost-optimized content creation"""
        
        # Use ultra_cheap tier for budget-conscious requests
        topic = self._extract_topic(command)
        if not topic:
            return "❓ Please specify a topic for cost-optimized content creation."
        
        try:
            result = await self.mcp_orchestrator.generate_content_with_model_selection(
                topic=topic,
                content_type="blog_post", 
                cost_tier="ultra_cheap"  # Use cheapest available models
            )
            
            if result.get("success"):
                metadata = result["generation_metadata"]
                content = result["content"]
                
                return f"""💰 **Cost-Optimized Content Generated:**

📝 **Title:** {content['title']}
🤖 **Model Used:** {metadata.get('model_used')} ({metadata.get('provider')})
🎯 **Tokens:** {metadata.get('tokens_used')}
💵 **Cost:** ${metadata.get('estimated_cost', 0):.4f}

The content has been generated using the most cost-effective model available. You can now review and publish it to your CMS."""
            else:
                return f"❌ Cost-optimized generation failed: {result.get('error')}"
                
        except Exception as e:
            return f"❌ Error in cost-optimized content creation: {str(e)}"
    
    async def _handle_mcp_status(self) -> str:
        """Handle MCP status requests"""
        try:
            if not self.mcp_enabled:
                return "❌ MCP capabilities are not enabled"
            
            status = await self.mcp_orchestrator.get_orchestrator_status()
            
            if status.get("error"):
                return f"❌ Error getting MCP status: {status['error']}"
            
            mcp_servers = status.get("mcp_servers", {})
            
            response = "🔧 **MCP System Status:**\n\n"
            response += f"**Status:** {status.get('status', 'unknown').title()}\n"
            response += f"**Connected Servers:** {mcp_servers.get('total_servers', 0)}\n"
            response += f"**Available Models:** {status.get('available_models', 0)}\n"
            response += f"**Total Tools:** {mcp_servers.get('total_tools', 0)}\n"
            response += f"**Total Resources:** {mcp_servers.get('total_resources', 0)}\n\n"
            
            response += "**Server Details:**\n"
            for server_name, server_info in mcp_servers.get("servers", {}).items():
                server_status = server_info.get("status", "unknown")
                status_icon = "✅" if server_status == "connected" else "❌"
                response += f"{status_icon} {server_name}: {server_status}\n"
            
            return response
            
        except Exception as e:
            return f"❌ Error getting MCP status: {str(e)}"
    
    def _extract_topic(self, command: str) -> str:
        """Extract topic from command string"""
        
        # Common patterns to extract topic
        patterns = [
            "create content about ",
            "write article about ",
            "blog post about ",
            "generate content about ",
            "content on ",
            "article on ",
            "write about ",
            "create about "
        ]
        
        command_lower = command.lower()
        
        for pattern in patterns:
            if pattern in command_lower:
                topic_start = command_lower.find(pattern) + len(pattern)
                topic = command[topic_start:].strip()
                
                # Remove common trailing words
                for ending in [" please", " now", " today"]:
                    if topic.lower().endswith(ending):
                        topic = topic[:-len(ending)].strip()
                
                return topic
        
        # If no pattern found, try to guess from keywords
        keywords = command.split()
        if len(keywords) > 2:
            # Skip first few command words, take the rest as topic
            for i, word in enumerate(keywords):
                if word.lower() in ["about", "on", "regarding", "concerning"]:
                    return " ".join(keywords[i+1:])
        
        return ""
    
    async def get_enhanced_status(self) -> Dict[str, Any]:
        """Get comprehensive status including MCP capabilities"""
        
        # Get original status
        original_status = {}
        if hasattr(self.original_orchestrator, 'get_status'):
            original_status = self.original_orchestrator.get_status()
        
        # Add MCP status
        mcp_status = {}
        if self.mcp_enabled:
            try:
                mcp_status = await self.mcp_orchestrator.get_orchestrator_status()
            except Exception as e:
                mcp_status = {"error": str(e)}
        
        return {
            "enhanced_cofounder": {
                "mcp_enabled": self.mcp_enabled,
                "mcp_available": MCP_AVAILABLE,
                "status": "operational" if self.mcp_enabled else "limited"
            },
            "original_orchestrator": original_status,
            "mcp_orchestrator": mcp_status
        }