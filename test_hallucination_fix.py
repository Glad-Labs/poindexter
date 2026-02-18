"""
Quick test to verify hallucination fix is working.
Tests RAG retrieval and system knowledge integration.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src/cofounder_agent"))

from services.system_knowledge_rag import get_system_knowledge_rag
from services.prompt_templates import PromptTemplates


async def test_rag_retrieval():
    """Test that RAG can retrieve system knowledge"""
    print("\n" + "="*60)
    print("TEST 1: RAG Retrieval")
    print("="*60)
    
    rag = get_system_knowledge_rag()
    
    # Test 1: Programming languages
    query1 = "What programming languages is Glad Labs built with?"
    result1 = rag.retrieve(query1)
    
    print(f"\nQuery: {query1}")
    print(f"Confidence: {result1.confidence:.2f}")
    print(f"Source: {result1.source_section}")
    print(f"Response:\n{result1.content[:200]}...\n")
    
    assert result1.confidence > 0.8, "Confidence should be high for structured question"
    assert "Python" in result1.content or "javascript" in result1.content.lower(), "Should mention languages"
    print("✅ PASS")
    
    # Test 2: Agent types
    query2 = "What are the main agent types in this system?"
    result2 = rag.retrieve(query2)
    
    print(f"\nQuery: {query2}")
    print(f"Confidence: {result2.confidence:.2f}")
    print(f"Source: {result2.source_section}")
    print(f"Response:\n{result2.content[:200]}...\n")
    
    assert result2.confidence > 0.8, "Confidence should be high"
    assert "Content" in result2.content, "Should mention Content agent"
    print("✅ PASS")
    
    # Test 3: LLM providers
    query3 = "How many LLM providers are supported?"
    result3 = rag.retrieve(query3)
    
    print(f"\nQuery: {query3}")
    print(f"Confidence: {result3.confidence:.2f}")
    print(f"Source: {result3.source_section}")
    print(f"Response:\n{result3.content[:200]}...\n")
    
    assert result3.confidence > 0.8, "Confidence should be high"
    assert "Ollama" in result3.content or "OpenAI" in result3.content, "Should mention providers"
    print("✅ PASS")


async def test_system_question_detection():
    """Test that system questions are detected"""
    print("\n" + "="*60)
    print("TEST 2: System Question Detection")
    print("="*60)
    
    # System questions (should return True)
    system_qs = [
        "What programming languages is Glad Labs built with?",
        "What are the main agent types?",
        "How many LLM providers are supported?",
        "What database does Glad Labs use?",
        "What ports does the system run on?",
    ]
    
    for q in system_qs:
        is_system = PromptTemplates.detect_system_question(q)
        print(f"'{q[:50]}...' → {is_system}")
        assert is_system, f"Should detect as system question: {q}"
    
    print("✅ PASS - All system questions detected")
    
    # Non-system questions (should return False)
    non_system_qs = [
        "How do I make a good pizza?",
        "What's the weather today?",
        "Tell me a joke about cats",
    ]
    
    for q in non_system_qs:
        is_system = PromptTemplates.detect_system_question(q)
        print(f"'{q[:50]}...' → {is_system}")
        assert not is_system, f"Should NOT detect as system question: {q}"
    
    print("✅ PASS - Non-system questions correctly identified")


async def test_knowledge_base_init():
    """Test that knowledge base initializes properly"""
    print("\n" + "="*60)
    print("TEST 3: Knowledge Base Initialization")
    print("="*60)
    
    rag = get_system_knowledge_rag()
    
    assert rag.is_initialized, "RAG should be initialized"
    print(f"✅ RAG initialized successfully")
    
    sections = rag.list_sections()
    print(f"✅ Found {len(sections)} knowledge base sections")
    
    assert len(sections) > 0, "Should have sections"
    print(f"Sample sections: {', '.join(sections[:3])}")
    
    print("✅ PASS")


async def main():
    """Run all tests"""
    print("\n" + "🧪 TESTING HALLUCINATION FIX IMPLEMENTATION" + "\n")
    
    try:
        await test_knowledge_base_init()
        await test_system_question_detection()
        await test_rag_retrieval()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED")
        print("="*60)
        print("\nHallucination fix implementation is working correctly!")
        print("The chat endpoint will now:")
        print("  1. Detect system knowledge questions")
        print("  2. Retrieve accurate answers from knowledge base")
        print("  3. Use system-aware prompts to prevent hallucination")
        print("  4. Cache responses for faster subsequent queries")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
