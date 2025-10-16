"""
MCP-Enhanced Content Agent Orchestrator for GLAD Labs

This orchestrator integrates with MCP servers to provide flexible AI model selection
and standardized content management operations.
"""

import asyncio
import logging
import sys
import os
from typing import Any, Dict, List, Optional

# Add paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from mcp.client_manager import MCPClientManager


class MCPContentOrchestrator:
    """
    Enhanced content orchestrator using MCP for AI model selection and content management.
    
    This orchestrator provides:
    1. Flexible AI model selection with cost optimization
    2. Standardized content management through MCP
    3. Dynamic capability discovery
    4. Improved error handling and logging
    """
    
    def __init__(self):
        self.logger = logging.getLogger("mcp.content_orchestrator")
        self.mcp_manager = MCPClientManager()
        self.initialized = False
        
        # Task tracking
        self.current_task = None
        self.task_history = []
    
    async def initialize(self):
        """Initialize MCP connections and discover capabilities"""
        try:
            # Import and register MCP servers
            from mcp.servers.ai_model_server import AIModelServer
            from mcp.servers.strapi_server import StrapiMCPServer
            
            # Register AI Model Server
            ai_server = AIModelServer()
            await self.mcp_manager.register_server("ai-models", ai_server)
            
            # Register Strapi Server
            strapi_server = StrapiMCPServer()
            await self.mcp_manager.register_server("strapi-cms", strapi_server)
            
            self.initialized = True
            self.logger.info("MCP Content Orchestrator initialized successfully")
            
            # Log discovered capabilities
            capabilities = await self.mcp_manager.discover_capabilities()
            self.logger.info(f"Discovered {capabilities['total_capabilities']} total capabilities")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize MCP orchestrator: {e}")
            return False
    
    async def generate_content_with_model_selection(self, 
                                                   topic: str,
                                                   content_type: str = "blog_post",
                                                   cost_tier: str = "balanced",
                                                   specific_model: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate content using flexible AI model selection.
        
        Args:
            topic: Content topic
            content_type: Type of content to generate
            cost_tier: Cost optimization level (ultra_cheap, cheap, balanced, premium, ultra_premium)
            specific_model: Specific model to use (overrides cost_tier)
            
        Returns:
            Generated content with metadata
        """
        if not self.initialized:
            return {"error": "Orchestrator not initialized"}
        
        try:
            self.logger.info(f"Generating {content_type} about '{topic}' using {cost_tier} tier")
            
            # Create content generation prompt
            prompt = self._create_content_prompt(topic, content_type)
            
            # Generate content using MCP AI model server
            generation_args = {
                "prompt": prompt,
                "cost_tier": cost_tier
            }
            
            if specific_model:
                generation_args["specific_model"] = specific_model
            
            result = await self.mcp_manager.call_tool("generate_text", generation_args)
            
            if result.get("error"):
                self.logger.error(f"Content generation failed: {result['error']}")
                return result
            
            # Process and structure the generated content
            structured_content = self._process_generated_content(result["text"], topic)
            
            return {
                "success": True,
                "content": structured_content,
                "generation_metadata": {
                    "model_used": result.get("model_used"),
                    "provider": result.get("provider"),
                    "cost_tier": result.get("cost_tier"),
                    "tokens_used": result.get("tokens_used", 0),
                    "estimated_cost": result.get("estimated_cost", 0.0)
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error in content generation: {e}")
            return {"error": str(e)}
    
    async def research_and_create_post(self, 
                                      topic: str,
                                      target_audience: str = "general",
                                      category: str = "",
                                      tags: List[str] = None) -> Dict[str, Any]:
        """
        Complete content creation pipeline: research -> generate -> review -> publish.
        
        Args:
            topic: Content topic
            target_audience: Target audience
            category: Content category
            tags: Content tags
            
        Returns:
            Complete workflow result
        """
        if not self.initialized:
            return {"error": "Orchestrator not initialized"}
        
        workflow_result = {
            "topic": topic,
            "stages": {},
            "success": False,
            "final_post_id": None
        }
        
        try:
            # Stage 1: Research (use cheap model for research)
            self.logger.info(f"Stage 1: Researching topic '{topic}'")
            research_prompt = f"""
            Research the topic: {topic}
            
            Provide:
            1. Key points to cover
            2. Current trends and insights
            3. Target audience considerations for: {target_audience}
            4. SEO keywords to include
            5. Potential subtopics
            
            Format as structured research notes.
            """
            
            research_result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": research_prompt,
                "cost_tier": "cheap",  # Use cheap model for research
                "max_tokens": 2000
            })
            
            workflow_result["stages"]["research"] = research_result
            
            if research_result.get("error"):
                return workflow_result
            
            # Stage 2: Content Creation (use balanced model for main content)
            self.logger.info("Stage 2: Creating main content")
            content_result = await self.generate_content_with_model_selection(
                topic=topic,
                content_type="blog_post",
                cost_tier="balanced"
            )
            
            workflow_result["stages"]["content_creation"] = content_result
            
            if not content_result.get("success"):
                return workflow_result
            
            # Stage 3: Quality Review (use cheap model for QA)
            self.logger.info("Stage 3: Quality assurance review")
            qa_prompt = f"""
            Review the following blog post for quality, accuracy, and readability:
            
            Title: {content_result['content']['title']}
            Content: {content_result['content']['content'][:1000]}...
            
            Provide:
            1. Quality score (1-10)
            2. Specific improvements needed
            3. SEO optimization suggestions
            4. Readability assessment
            
            Be concise and actionable.
            """
            
            qa_result = await self.mcp_manager.call_tool("generate_text", {
                "prompt": qa_prompt,
                "cost_tier": "cheap",
                "max_tokens": 1000
            })
            
            workflow_result["stages"]["quality_review"] = qa_result
            
            # Stage 4: Publishing to Strapi
            self.logger.info("Stage 4: Publishing to CMS")
            
            content = content_result["content"]
            publish_result = await self.mcp_manager.call_tool("create_post", {
                "title": content["title"],
                "content": content["content"],
                "excerpt": content.get("excerpt", ""),
                "category": category,
                "tags": tags or [],
                "featured": False
            })
            
            workflow_result["stages"]["publishing"] = publish_result
            
            if publish_result.get("success"):
                workflow_result["success"] = True
                workflow_result["final_post_id"] = publish_result.get("post_id")
                self.logger.info(f"Content creation workflow completed successfully. Post ID: {publish_result.get('post_id')}")
            
            # Calculate total costs
            total_cost = 0.0
            total_tokens = 0
            for stage, result in workflow_result["stages"].items():
                if isinstance(result, dict) and "generation_metadata" in result:
                    total_cost += result["generation_metadata"].get("estimated_cost", 0.0)
                    total_tokens += result["generation_metadata"].get("tokens_used", 0)
                elif isinstance(result, dict) and "estimated_cost" in result:
                    total_cost += result.get("estimated_cost", 0.0)
                    total_tokens += result.get("tokens_used", 0)
            
            workflow_result["total_cost"] = total_cost
            workflow_result["total_tokens"] = total_tokens
            
            return workflow_result
            
        except Exception as e:
            self.logger.error(f"Error in content creation workflow: {e}")
            workflow_result["error"] = str(e)
            return workflow_result
    
    def _create_content_prompt(self, topic: str, content_type: str) -> str:
        """Create optimized prompts for different content types"""
        
        if content_type == "blog_post":
            return f"""
            Write a comprehensive, engaging blog post about: {topic}
            
            Requirements:
            - Create an attention-grabbing title
            - Write 800-1200 words of high-quality content
            - Include an engaging introduction
            - Use clear headings and subheadings
            - Provide practical insights and actionable advice
            - Write a compelling conclusion
            - Optimize for SEO with natural keyword usage
            - Use a professional yet conversational tone
            
            Format the output as:
            TITLE: [Your title here]
            EXCERPT: [2-3 sentence summary]
            CONTENT: [Full blog post content in markdown format]
            """
        
        elif content_type == "summary":
            return f"""
            Create a concise, informative summary about: {topic}
            
            Requirements:
            - 200-300 words maximum
            - Cover key points and main insights
            - Use clear, accessible language
            - Focus on the most important information
            """
        
        else:
            return f"Write high-quality content about: {topic}"
    
    def _process_generated_content(self, raw_text: str, topic: str) -> Dict[str, str]:
        """Process and structure generated content"""
        
        lines = raw_text.split('\n')
        
        title = ""
        excerpt = ""
        content = ""
        
        current_section = None
        
        for line in lines:
            line = line.strip()
            
            if line.startswith("TITLE:"):
                title = line.replace("TITLE:", "").strip()
                current_section = "title"
            elif line.startswith("EXCERPT:"):
                excerpt = line.replace("EXCERPT:", "").strip()
                current_section = "excerpt"
            elif line.startswith("CONTENT:"):
                content = line.replace("CONTENT:", "").strip()
                current_section = "content"
            elif current_section == "excerpt" and line and not line.startswith("CONTENT:"):
                excerpt += " " + line
            elif current_section == "content" and line:
                content += "\n" + line
        
        # Fallbacks if sections not found
        if not title:
            title = f"Insights on {topic}"
        
        if not excerpt:
            # Generate excerpt from first paragraph of content
            content_lines = content.split('\n')
            for content_line in content_lines[:3]:
                if len(content_line.strip()) > 50:
                    excerpt = content_line.strip()[:200] + "..."
                    break
        
        if not content:
            content = raw_text
        
        return {
            "title": title,
            "excerpt": excerpt,
            "content": content.strip()
        }
    
    async def get_available_models(self) -> Dict[str, Any]:
        """Get information about available AI models"""
        if not self.initialized:
            return {"error": "Orchestrator not initialized"}
        
        return await self.mcp_manager.call_tool("get_available_models", {})
    
    async def get_content_stats(self) -> Dict[str, Any]:
        """Get content management statistics"""
        if not self.initialized:
            return {"error": "Orchestrator not initialized"}
        
        return await self.mcp_manager.call_tool("get_content_stats", {})
    
    async def get_orchestrator_status(self) -> Dict[str, Any]:
        """Get comprehensive orchestrator status"""
        if not self.initialized:
            return {"error": "Orchestrator not initialized"}
        
        try:
            mcp_status = await self.mcp_manager.get_server_status()
            models_info = await self.get_available_models()
            content_stats = await self.get_content_stats()
            
            return {
                "orchestrator": "MCP-Enhanced Content Orchestrator",
                "status": "operational",
                "mcp_servers": mcp_status,
                "available_models": models_info.get("total_models", 0),
                "content_stats": content_stats,
                "task_history_count": len(self.task_history)
            }
            
        except Exception as e:
            return {"error": str(e)}


# Testing and example usage
async def main():
    """
    Test the MCP-Enhanced Content Orchestrator
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    orchestrator = MCPContentOrchestrator()
    
    print("=== MCP Content Orchestrator Test ===")
    
    # Initialize
    print("\n1. Initializing orchestrator...")
    success = await orchestrator.initialize()
    if not success:
        print("❌ Failed to initialize orchestrator")
        return
    
    print("✅ Orchestrator initialized successfully")
    
    # Get status
    print("\n2. Orchestrator Status:")
    status = await orchestrator.get_orchestrator_status()
    print(f"   - MCP Servers: {status.get('mcp_servers', {}).get('total_servers', 0)}")
    print(f"   - Available Models: {status.get('available_models', 0)}")
    
    # Show available models
    print("\n3. Available AI Models:")
    models = await orchestrator.get_available_models()
    if models.get("available_models"):
        for tier, model_list in models["available_models"].items():
            print(f"   - {tier}: {len(model_list)} models")
    
    # Test simple content generation
    print("\n4. Testing Content Generation (Cheap Tier):")
    test_topic = "Benefits of AI in Content Creation"
    
    content_result = await orchestrator.generate_content_with_model_selection(
        topic=test_topic,
        content_type="blog_post",
        cost_tier="cheap"
    )
    
    if content_result.get("success"):
        metadata = content_result["generation_metadata"]
        print(f"   ✅ Content generated successfully")
        print(f"   - Model: {metadata.get('model_used')} ({metadata.get('provider')})")
        print(f"   - Tokens: {metadata.get('tokens_used')}")
        print(f"   - Cost: ${metadata.get('estimated_cost', 0):.4f}")
        print(f"   - Title: {content_result['content']['title']}")
    else:
        print(f"   ❌ Content generation failed: {content_result.get('error')}")
    
    print("\n=== Test Complete ===")


if __name__ == "__main__":
    asyncio.run(main())