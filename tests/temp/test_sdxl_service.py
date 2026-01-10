# -*- coding: utf-8 -*-
import asyncio
import sys
import os
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'cofounder_agent'))

async def test_sdxl():
    """Test SDXL generation directly"""
    logger.info("Testing SDXL image generation...")
    
    try:
        from services.image_service import ImageService
        
        # Create service instance
        service = ImageService()
        logger.info("✅ ImageService created")
        
        # Try generating an image
        output_path = "test_sdxl_output.png"
        logger.info(f"Attempting SDXL generation with output: {output_path}")
        
        success = await service.generate_image(
            prompt="sustainable energy solar panels wind turbines",
            output_path=output_path,
            num_inference_steps=5,  # Just 5 steps for speed
            guidance_scale=7.5,
            use_refinement=False  # Disable refinement for speed
        )
        
        if success:
            logger.info(f"✅ SDXL generation successful!")
            logger.info(f"   Output: {output_path}")
            if os.path.exists(output_path):
                size_mb = os.path.getsize(output_path) / (1024**2)
                logger.info(f"   File size: {size_mb:.2f}MB")
        else:
            logger.error("❌ SDXL generation failed!")
            
    except Exception as e:
        logger.error(f"❌ Error during SDXL test: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_sdxl())
