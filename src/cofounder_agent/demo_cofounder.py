"""
Test and demonstration script for the Intelligent AI Co-Founder system.

This script showcases the comprehensive AI business partner capabilities including:
- Context-aware conversations
- Business intelligence integration
- Memory and learning systems
- Strategic insights and recommendations
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# Add the parent directory to the path to import our modules
sys.path.append(str(Path(__file__).parent.parent))

try:
    from cofounder_agent.intelligent_cofounder import IntelligentCoFounder
    from cofounder_agent.business_intelligence import BusinessIntelligenceSystem
    from cofounder_agent.memory_system import AIMemorySystem, MemoryType, ImportanceLevel
except ImportError as e:
    print(f"Import error: {e}")
    print("Please ensure all dependencies are installed:")
    print("pip install -r requirements.txt")
    sys.exit(1)


async def demo_ai_cofounder():
    """Demonstrate the AI Co-Founder capabilities"""
    
    print("🚀 " + "="*60)
    print("    GLAD LABS INTELLIGENT AI CO-FOUNDER DEMO")
    print("="*64)
    print()
    
    # Initialize the AI Co-Founder
    print("📋 Initializing AI Co-Founder...")
    cofounder = IntelligentCoFounder("GLAD Labs")
    
    success = await cofounder.initialize()
    if not success:
        print("❌ Failed to initialize AI Co-Founder")
        return
    
    print("✅ AI Co-Founder ready!")
    print()
    
    # Test business intelligence gathering
    print("🔍 Gathering Business Intelligence...")
    try:
        dashboard = await cofounder.business_intelligence.get_dashboard_data()
        
        print(f"📊 Business Intelligence Summary:")
        print(f"   • Total metrics collected: {dashboard['data_quality']['total_metrics']}")
        print(f"   • Data sources: {dashboard['data_quality']['data_sources']}")
        print(f"   • Data confidence: {dashboard['data_quality']['confidence_score']:.1%}")
        print(f"   • Strategic insights: {len(dashboard['strategic_insights'])}")
        print()
    except Exception as e:
        print(f"⚠️  Business Intelligence limited: {e}")
        print()
    
    # Test memory system
    print("🧠 Testing Memory System...")
    try:
        memory_summary = await cofounder.memory_system.get_memory_summary()
        
        print(f"💭 Memory System Summary:")
        print(f"   • Total memories: {memory_summary['total_memories']}")
        print(f"   • User preferences: {memory_summary['total_preferences']}")
        print(f"   • Knowledge clusters: {memory_summary['total_knowledge_clusters']}")
        print(f"   • Embedding model: {'Active' if memory_summary['embedding_model_active'] else 'Inactive'}")
        print()
    except Exception as e:
        print(f"⚠️  Memory System limited: {e}")
        print()
    
    # Interactive conversation demo
    print("💬 Interactive Conversation Demo")
    print("   (Type 'quit' to exit)")
    print("-" * 40)
    
    # Predefined demo conversations if running non-interactively
    demo_messages = [
        "How is GLAD Labs doing overall?",
        "What's our content strategy looking like?", 
        "I want to focus on growing revenue. What should I prioritize?",
        "How can we optimize costs while scaling up?",
        "What are the biggest opportunities for growth right now?"
    ]
    
    try:
        # Check if running interactively
        import sys
        interactive = sys.stdin.isatty()
        
        if interactive:
            print("🎯 Interactive mode - you can ask questions!")
            while True:
                try:
                    user_input = input("\n👤 You: ").strip()
                    if user_input.lower() in ['quit', 'exit', 'q']:
                        break
                    
                    if user_input:
                        print("🤖 AI Co-Founder: Thinking...")
                        response = await cofounder.chat(user_input)
                        print(f"🤖 {response}")
                        
                except KeyboardInterrupt:
                    print("\n\n👋 Goodbye!")
                    break
                except EOFError:
                    break
        else:
            print("🎯 Demo mode - running predefined conversations...")
            for i, message in enumerate(demo_messages, 1):
                print(f"\n{i}. 👤 User: {message}")
                try:
                    response = await cofounder.chat(message)
                    print(f"   🤖 {response[:200]}..." if len(response) > 200 else f"   🤖 {response}")
                except Exception as e:
                    print(f"   ❌ Error: {e}")
                    
                # Small delay for readability
                await asyncio.sleep(0.5)
    
    except Exception as e:
        print(f"❌ Conversation error: {e}")
    
    # Generate business report
    print("\n" + "="*60)
    print("📈 GENERATING BUSINESS REPORT")
    print("="*60)
    
    try:
        report = await cofounder.generate_business_report()
        print(report)
    except Exception as e:
        print(f"❌ Could not generate business report: {e}")
    
    print("\n" + "="*60)
    print("✅ AI CO-FOUNDER DEMO COMPLETE")
    print("="*60)


async def test_individual_systems():
    """Test individual systems separately"""
    
    print("\n🔧 TESTING INDIVIDUAL SYSTEMS")
    print("="*40)
    
    # Test Business Intelligence System
    print("\n1. Testing Business Intelligence System...")
    try:
        bi_system = BusinessIntelligenceSystem()
        metrics = await bi_system.collect_all_metrics()
        print(f"   ✅ Collected {sum(len(m) for m in metrics.values())} metrics from {len(metrics)} sources")
    except Exception as e:
        print(f"   ❌ Business Intelligence error: {e}")
    
    # Test Memory System
    print("\n2. Testing AI Memory System...")
    try:
        memory_system = AIMemorySystem()
        
        # Store a test memory
        memory_id = await memory_system.store_memory(
            "GLAD Labs is an AI content automation platform",
              MemoryType.BUSINESS_FACT
        )
        
        # Recall memories
        memories = await memory_system.recall_memories("AI content")
        print(f"   ✅ Stored memory and recalled {len(memories)} relevant memories")
    except Exception as e:
        print(f"   ❌ Memory System error: {e}")
    
    print("\n✅ Individual system tests complete")


def main():
    """Main entry point"""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    print("🧪 AI Co-Founder System Test & Demo")
    print("====================================")
    
    # Run the demos
    asyncio.run(demo_ai_cofounder())
    
    # Test individual systems
    asyncio.run(test_individual_systems())


if __name__ == "__main__":
    main()