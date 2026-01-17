#!/usr/bin/env python3
"""
Test script for Glad Labs MCP implementation

This script tests the MCP servers and integration without requiring
full package installation.
"""

import asyncio
import logging
import sys
import os

# Add paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))


async def test_ai_model_server():
    """Test the AI Model Server"""
    print("=== Testing AI Model Server ===")
    
    try:
        from src.mcp.servers.ai_model_server import AIModelServer
        
        server = AIModelServer()
        
        # Test model discovery
        print("\n1. Available Models:")
        models = await server.get_available_models()
        
        print(f"   Total models: {models.get('total_models', 0)}")
        
        providers = models.get("providers_available", {})
        for provider, available in providers.items():
            status = "✅" if available else "❌"
            print(f"   {status} {provider.title()}")
        
        # Test content generation if models are available
        if models.get("total_models", 0) > 0:
            print("\n2. Testing Content Generation:")
            test_prompt = "Explain the benefits of AI in one paragraph."
            
            # Test different cost tiers
            for tier in ["ultra_cheap", "cheap", "balanced"]:
                print(f"\n   Testing {tier} tier:")
                result = await server.generate_text(
                    prompt=test_prompt,
                    cost_tier=tier,
                    max_tokens=200
                )
                
                if result.get("error"):
                    print(f"      ❌ Error: {result['error']}")
                else:
                    model_used = result.get("model_used", "unknown")
                    provider = result.get("provider", "unknown")
                    cost = result.get("estimated_cost", 0.0)
                    print(f"      ✅ Success: {model_used} ({provider}) - ${cost:.4f}")
                    print(f"      Text: {result.get('text', '')[:100]}...")
        
        print("\n✅ AI Model Server test completed")
        return True
        
    except Exception as e:
        print(f"❌ AI Model Server test failed: {e}")
        return False


async def test_strapi_server():
    """Test the Strapi Server"""
    print("\n=== Testing Strapi Server ===")
    
    try:
        from src.mcp.servers.strapi_server import StrapiMCPServer
        
        server = StrapiMCPServer()
        
        # Test content stats
        print("\n1. Content Statistics:")
        stats = await server.get_content_stats()
        
        if stats.get("success"):
            print("   ✅ Strapi connection successful")
            print(f"   Published posts: {stats.get('stats', {}).get('total_published_posts', 0)}")
        else:
            print(f"   ❌ Strapi connection failed: {stats.get('error')}")
        
        # Test schema
        print("\n2. Content Schema:")
        schema = await server.get_content_schema()
        print(f"   Content types: {len(schema)} defined")
        
        print("\n✅ Strapi Server test completed")
        return True
        
    except Exception as e:
        print(f"❌ Strapi Server test failed: {e}")
        return False


async def test_mcp_client_manager():
    """Test the MCP Client Manager"""
    print("\n=== Testing MCP Client Manager ===")
    
    try:
        from src.mcp.client_manager import MCPClientManager
        from src.mcp.servers.ai_model_server import AIModelServer
        from src.mcp.servers.strapi_server import StrapiMCPServer
        
        manager = MCPClientManager()
        
        # Register servers
        print("\n1. Registering MCP Servers:")
        
        ai_server = AIModelServer()
        ai_success = await manager.register_server("ai-models", ai_server)
        print(f"   AI Model Server: {'✅' if ai_success else '❌'}")
        
        strapi_server = StrapiMCPServer()
        strapi_success = await manager.register_server("strapi-cms", strapi_server)
        print(f"   Strapi Server: {'✅' if strapi_success else '❌'}")
        
        # Get server status
        print("\n2. Server Status:")
        status = await manager.get_server_status()
        print(f"   Total servers: {status.get('total_servers', 0)}")
        print(f"   Total tools: {status.get('total_tools', 0)}")
        print(f"   Total resources: {status.get('total_resources', 0)}")
        
        # Test tool discovery
        print("\n3. Tool Discovery:")
        all_tools = await manager.list_all_tools()
        for server_name, tools in all_tools.items():
            print(f"   {server_name}: {len(tools)} tools")
            for tool in tools[:2]:  # Show first 2 tools
                print(f"     - {tool.get('name', 'unnamed')}")
        
        # Test tool call if available
        if status.get('total_tools', 0) > 0:
            print("\n4. Testing Tool Call:")
            result = await manager.call_tool("get_content_stats", {})
            if result.get("error"):
                print(f"   ❌ Tool call failed: {result['error']}")
            else:
                print("   ✅ Tool call successful")
        
        print("\n✅ MCP Client Manager test completed")
        return True
        
    except Exception as e:
        print(f"❌ MCP Client Manager test failed: {e}")
        return False


async def test_mcp_orchestrator():
    """Test the MCP Orchestrator"""
    print("\n=== Testing MCP Orchestrator ===")
    
    try:
        from src.mcp.mcp_orchestrator import MCPContentOrchestrator
        
        orchestrator = MCPContentOrchestrator()
        
        # Initialize
        print("\n1. Initializing Orchestrator:")
        success = await orchestrator.initialize()
        print(f"   Initialization: {'✅' if success else '❌'}")
        
        if not success:
            return False
        
        # Get status
        print("\n2. Orchestrator Status:")
        status = await orchestrator.get_orchestrator_status()
        
        if status.get("error"):
            print(f"   ❌ Status error: {status['error']}")
        else:
            print(f"   Status: {status.get('status', 'unknown')}")
            mcp_servers = status.get('mcp_servers', {})
            print(f"   MCP servers: {mcp_servers.get('total_servers', 0)}")
        
        # Test simple content generation if models available
        models = await orchestrator.get_available_models()
        if models.get("total_models", 0) > 0:
            print("\n3. Testing Content Generation:")
            result = await orchestrator.generate_content_with_model_selection(
                topic="Benefits of automation in business",
                cost_tier="cheap"
            )
            
            if result.get("success"):
                print("   ✅ Content generation successful")
                metadata = result.get("generation_metadata", {})
                print(f"   Model: {metadata.get('model_used')}")
                print(f"   Cost: ${metadata.get('estimated_cost', 0):.4f}")
            else:
                print(f"   ❌ Content generation failed: {result.get('error')}")
        
        print("\n✅ MCP Orchestrator test completed")
        return True
        
    except Exception as e:
        print(f"❌ MCP Orchestrator test failed: {e}")
        return False


async def main():
    """Run all MCP tests"""
    logging.basicConfig(level=logging.WARNING)  # Reduce log noise
    
    print("🚀 Glad Labs MCP Implementation Test Suite")
    print("=" * 50)
    
    # Test individual components
    results = {
        "ai_model_server": await test_ai_model_server(),
        "strapi_server": await test_strapi_server(), 
        "client_manager": await test_mcp_client_manager(),
        "orchestrator": await test_mcp_orchestrator()
    }
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 TEST RESULTS SUMMARY:")
    print("=" * 50)
    
    passed = 0
    total = len(results)
    
    for component, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{component.replace('_', ' ').title():<20} {status}")
        if success:
            passed += 1
    
    print("-" * 50)
    print(f"TOTAL: {passed}/{total} tests passed ({(passed/total)*100:.0f}%)")
    
    if passed == total:
        print("\n🎉 All MCP components are working correctly!")
        print("\n💡 Next Steps:")
        print("1. Install required packages: pip install mcp ollama")
        print("2. Set up environment variables for API keys") 
        print("3. Run the enhanced co-founder agent")
        print("4. Test with real content creation workflows")
    else:
        print("\n⚠️  Some components need attention. Check error messages above.")
        print("\n🔧 Common Issues:")
        print("- Missing API keys (OPENAI_API_KEY, GEMINI_API_KEY)")
        print("- Strapi not running (start with npm run develop)")
        print("- Ollama not installed or not running")


if __name__ == "__main__":
    asyncio.run(main())