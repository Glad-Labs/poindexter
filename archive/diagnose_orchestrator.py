#!/usr/bin/env python3
"""
Diagnostic script to debug ContentOrchestrator exception
"""
import asyncio
import logging
import sys
import traceback
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('orchestrator_diagnostic.log')
    ]
)
logger = logging.getLogger(__name__)

async def test_orchestrator_directly():
    """Test ContentOrchestrator.run() directly with full exception capture"""
    logger.info("="*80)
    logger.info("STARTING CONTENTORCHESTRATOR DIAGNOSTIC TEST")
    logger.info("="*80)
    
    try:
        # Import UnifiedOrchestrator instead (ContentOrchestrator is deprecated)
        logger.info("Importing UnifiedOrchestrator...")
        from cofounder_agent.services.unified_orchestrator import UnifiedOrchestrator
        logger.info("✅ UnifiedOrchestrator imported successfully")
        
        # Create instance
        logger.info("Creating UnifiedOrchestrator instance...")
        orchestrator = UnifiedOrchestrator(
            database_service=None,
            model_router=None,
            quality_service=None,
            memory_system=None,
            content_orchestrator=None,
            financial_agent=None,
            compliance_agent=None,
        )
        logger.info("✅ UnifiedOrchestrator instance created")
        
        # Test with minimal parameters
        logger.info("-" * 80)
        logger.info("CALLING orchestrator.process_request() with minimal parameters")
        logger.info("-" * 80)
        
        try:
            # Use a natural language request instead
            user_request = "Create a blog post about Artificial Intelligence with professional tone"
            
            result = await orchestrator.process_request(user_request)
            logger.info("✅ UnifiedOrchestrator.process_request() completed successfully")
            logger.info(f"Result type: {type(result)}")
            if isinstance(result, dict):
                logger.info(f"Result status: {result.get('status')}")
                logger.info(f"Result keys: {list(result.keys())}")
            else:
                logger.info(f"Result: {result}")
            return result
            
        except Exception as stage_error:
            logger.error("❌ Exception during orchestrator.process_request()")
            logger.error(f"Exception type: {type(stage_error).__name__}")
            logger.error(f"Exception message: {str(stage_error)}")
            logger.error("Full traceback:")
            logger.error(traceback.format_exc())
            
            # Try to identify which stage failed
            logger.info("-" * 80)
            logger.info("ATTEMPTING TO IDENTIFY FAILING STAGE")
            logger.info("-" * 80)
            
            # Check if error message contains stage info
            error_str = str(stage_error)
            if "research" in error_str.lower():
                logger.error("🔴 Failure identified in RESEARCH stage")
            elif "creative" in error_str.lower():
                logger.error("🔴 Failure identified in CREATIVE stage")
            elif "qa" in error_str.lower():
                logger.error("🔴 Failure identified in QA stage")
            elif "image" in error_str.lower():
                logger.error("🔴 Failure identified in IMAGE stage")
            elif "format" in error_str.lower():
                logger.error("🔴 Failure identified in FORMATTING stage")
            else:
                logger.error("🔴 Failure stage unclear - check full traceback above")
            
            raise
    
    except ImportError as import_error:
        logger.error(f"❌ Import failed: {import_error}")
        logger.error("Checking if required modules exist...")
        
        # Check specific imports
        missing_modules = []
        
        modules_to_check = [
            "cofounder_agent.services.content_orchestrator",
            "agents.content_agent.agents.research_agent",
            "utils.constraint_utils",
        ]
        
        for module_name in modules_to_check:
            try:
                __import__(module_name)
                logger.info(f"✅ {module_name}")
            except ImportError as e:
                logger.error(f"❌ {module_name}: {e}")
                missing_modules.append((module_name, str(e)))
        
        if missing_modules:
            logger.error("\n" + "="*80)
            logger.error("MISSING MODULES DETECTED:")
            for module, error in missing_modules:
                logger.error(f"  - {module}")
                logger.error(f"    Error: {error}")
            logger.error("="*80)
        
        raise
    
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        logger.error(traceback.format_exc())
        raise

async def test_individual_agents():
    """Test individual agents to find which one is failing"""
    logger.info("\n" + "="*80)
    logger.info("TESTING INDIVIDUAL AGENTS")
    logger.info("="*80)
    
    agents_to_test = [
        ("ResearchAgent", "agents.content_agent.agents.research_agent", "ResearchAgent"),
        ("CreativeAgent", "agents.content_agent.agents.creative_agent", "CreativeAgent"),
        ("QAAgent", "agents.content_agent.agents.qa_agent", "QAAgent"),
        ("ImageAgent", "agents.content_agent.agents.image_agent", "ImageAgent"),
    ]
    
    results = {}
    
    for agent_name, module_path, class_name in agents_to_test:
        logger.info(f"\nTesting {agent_name}...")
        try:
            logger.info(f"  Importing {module_path}")
            module = __import__(module_path, fromlist=[class_name])
            agent_class = getattr(module, class_name)
            logger.info(f"  ✅ {agent_name} imported successfully")
            results[agent_name] = "AVAILABLE"
        except ImportError as e:
            logger.error(f"  ❌ {agent_name} import failed: {e}")
            results[agent_name] = f"MISSING: {str(e)}"
        except Exception as e:
            logger.error(f"  ❌ {agent_name} error: {e}")
            results[agent_name] = f"ERROR: {str(e)}"
    
    logger.info("\n" + "-"*80)
    logger.info("AGENT STATUS SUMMARY:")
    for agent_name, status in results.items():
        if status == "AVAILABLE":
            logger.info(f"  ✅ {agent_name}")
        else:
            logger.error(f"  ❌ {agent_name}: {status}")

async def main():
    logger.info("CONTENTORCHESTRATOR DIAGNOSTIC SUITE")
    logger.info("Starting at: " + str(Path.cwd()))
    
    try:
        # First test individual agents
        await test_individual_agents()
        
        # Then test orchestrator
        logger.info("\n")
        await test_orchestrator_directly()
        
    except Exception as e:
        logger.error(f"\n❌ DIAGNOSTIC FAILED: {e}")
        sys.exit(1)
    
    logger.info("\n" + "="*80)
    logger.info("DIAGNOSTIC COMPLETE")
    logger.info("Check orchestrator_diagnostic.log for full details")
    logger.info("="*80)

if __name__ == "__main__":
    asyncio.run(main())
