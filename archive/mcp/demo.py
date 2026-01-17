#!/usr/bin/env python3
"""
Glad Labs MCP Implementation Demonstration

This script demonstrates the MCP integration with cost optimization and flexible model selection.
"""

import asyncio
import logging
import os
import sys

# Add paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()


async def demonstrate_cost_optimization():
    """Demonstrate cost-optimized content generation"""
    print("=== Cost Optimization Demonstration ===")
    
    try:
        from mcp.servers.ai_model_server import AIModelServer
        
        server = AIModelServer()
        
        # Show available models
        print("\n1. Available Models by Cost Tier:")
        models_info = await server.get_available_models()
        
        available = models_info.get("available_models", {})
        providers = models_info.get("providers_available", {})
        
        print("   Providers:")
        for provider, is_available in providers.items():
            status = "✅" if is_available else "❌" 
            print(f"     {status} {provider.title()}")
        
        print("\n   Models by Cost Tier:")
        for tier, models in available.items():
            if models:
                print(f"     {tier}: {len(models)} models")
                for model in models[:1]:  # Show first model
                    cost = f"${model['cost_per_1k_tokens']:.3f}/1k tokens" if model['cost_per_1k_tokens'] > 0 else "Free"
                    print(f"       - {model['name']} ({model['provider']}) - {cost}")
        
        # Test content generation with different cost tiers
        test_prompt = "Write a brief explanation of how AI can help small businesses save time and money."
        
        if models_info.get("total_models", 0) > 0:
            print("\n2. Cost Tier Comparison:")
            
            for tier in ["ultra_cheap", "cheap", "balanced"]:
                if any(available.get(tier, [])):
                    print(f"\n   Testing {tier.upper()} tier:")
                    result = await server.generate_text(
                        prompt=test_prompt,
                        cost_tier=tier,
                        max_tokens=150
                    )
                    
                    if not result.get("error"):
                        model = result.get("model_used", "unknown")
                        provider = result.get("provider", "unknown")
                        cost = result.get("estimated_cost", 0.0)
                        tokens = result.get("tokens_used", 0)
                        
                        print(f"     ✅ Model: {model} ({provider})")
                        print(f"     💰 Cost: ${cost:.4f} ({tokens} tokens)")
                        print(f"     📝 Text: {result.get('text', '')[:80]}...")
                    else:
                        print(f"     ❌ Error: {result['error']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error in cost optimization demo: {e}")
        return False


async def demonstrate_content_workflow():
    """Demonstrate full content creation workflow"""
    print("\n=== Content Creation Workflow Demonstration ===")
    
    try:
        from mcp.mcp_orchestrator import MCPContentOrchestrator
        
        orchestrator = MCPContentOrchestrator()
        
        # Initialize
        print("\n1. Initializing MCP Orchestrator:")
        success = await orchestrator.initialize()
        
        if not success:
            print("   ❌ Initialization failed")
            return False
        
        print("   ✅ Orchestrator initialized")
        
        # Check available models
        models = await orchestrator.get_available_models()
        if models.get("total_models", 0) == 0:
            print("   ⚠️  No AI models available - need API keys or Ollama setup")
            return False
        
        print(f"   📊 Available models: {models.get('total_models', 0)}")
        
        # Test simple content generation
        print("\n2. Generating Content (Cost-Optimized):")
        topic = "5 Ways AI Can Streamline Business Operations"
        
        result = await orchestrator.generate_content_with_model_selection(
            topic=topic,
            content_type="blog_post",
            cost_tier="cheap"  # Use cost-effective model
        )
        
        if result.get("success"):
            content = result["content"]
            metadata = result["generation_metadata"]
            
            print("   ✅ Content generated successfully!")
            print(f"   📄 Title: {content['title']}")
            print(f"   🤖 Model: {metadata.get('model_used')} ({metadata.get('provider')})")
            print(f"   💰 Cost: ${metadata.get('estimated_cost', 0):.4f}")
            print(f"   🎯 Tokens: {metadata.get('tokens_used', 0)}")
            print(f"   📝 Excerpt: {content.get('excerpt', 'No excerpt')[:100]}...")
        else:
            print(f"   ❌ Content generation failed: {result.get('error')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error in workflow demo: {e}")
        return False


async def demonstrate_enhanced_cofounder():
    """Demonstrate enhanced co-founder agent integration"""
    print("\n=== Enhanced Co-Founder Agent Demonstration ===")
    
    try:
        from cofounder_agent.mcp_integration import MCPEnhancedCoFounder
        
        # Mock original orchestrator for demo
        class MockOrchestrator:
            def process_command(self, command, context=None):
                return f"Original orchestrator handled: {command}"
        
        original = MockOrchestrator()
        enhanced = MCPEnhancedCoFounder(original)
        
        # Initialize MCP
        print("\n1. Initializing Enhanced Co-Founder:")
        success = await enhanced.initialize_mcp()
        
        if success:
            print("   ✅ MCP capabilities enabled")
        else:
            print("   ⚠️  MCP capabilities limited - some features may not work")
        
        # Test enhanced commands
        print("\n2. Testing Enhanced Commands:")
        
        test_commands = [
            "What AI models are available?",
            "Create content about AI automation using cheap models",
            "Show MCP status and capabilities"
        ]
        
        for i, command in enumerate(test_commands, 1):
            print(f"\n   Test {i}: '{command}'")
            response = await enhanced.process_command_enhanced(command)
            print(f"   Response: {response[:200]}...")
        
        # Show comprehensive status
        print("\n3. System Status:")
        status = await enhanced.get_enhanced_status()
        
        enhanced_info = status.get("enhanced_cofounder", {})
        print(f"   MCP Enabled: {enhanced_info.get('mcp_enabled')}")
        print(f"   Status: {enhanced_info.get('status')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error in enhanced co-founder demo: {e}")
        return False


async def main():
    """Run the complete MCP demonstration"""
    
    # Setup minimal logging
    logging.basicConfig(level=logging.WARNING, format='%(name)s - %(levelname)s - %(message)s')
    
    print("🚀 Glad Labs MCP Integration Demonstration")
    print("=" * 60)
    
    # Check environment
    print("📋 Environment Check:")
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    
    print(f"   OpenAI API Key: {'✅' if has_openai else '❌'}")
    print(f"   Gemini API Key: {'✅' if has_gemini else '❌'}")
    
    if not (has_openai or has_gemini):
        print("\n⚠️  No AI API keys found. Add OPENAI_API_KEY or GEMINI_API_KEY to test AI features.")
        print("   You can still test the MCP infrastructure without AI capabilities.")
    
    # Run demonstrations
    demos = [
        ("Cost Optimization", demonstrate_cost_optimization),
        ("Content Workflow", demonstrate_content_workflow), 
        ("Enhanced Co-Founder", demonstrate_enhanced_cofounder)
    ]
    
    results = {}
    
    for demo_name, demo_func in demos:
        try:
            results[demo_name] = await demo_func()
        except Exception as e:
            print(f"❌ {demo_name} demonstration failed: {e}")
            results[demo_name] = False
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 DEMONSTRATION RESULTS:")
    print("=" * 60)
    
    for demo_name, success in results.items():
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"{demo_name:<20} {status}")
    
    successful = sum(results.values())
    total = len(results)
    
    print(f"\nTotal: {successful}/{total} demonstrations successful")
    
    if successful == total:
        print("\n🎉 MCP integration is fully operational!")
        print("\n💡 Key Benefits Achieved:")
        print("✅ Flexible AI model selection with cost optimization")
        print("✅ Standardized tool interface across all services")
        print("✅ Enhanced co-founder agent with MCP capabilities")
        print("✅ Runtime capability discovery and management")
    
    print("\n🔧 Next Steps:")
    print("1. Set up API keys for AI providers (OpenAI/Gemini)")
    print("2. Install Ollama for free local models during development")
    print("3. Integrate MCP commands into your existing workflows")
    print("4. Monitor costs and usage through MCP statistics")


if __name__ == "__main__":
    asyncio.run(main())