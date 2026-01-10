#!/usr/bin/env python3
"""Test SDXL optimization packages"""

import sys
sys.path.insert(0, 'src/cofounder_agent')
import warnings
import logging
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

logger.info("Checking optimization packages...\n")

try:
    import torch
    logger.info(f"OK PyTorch {torch.__version__}")
except ImportError:
    logger.error("FAILED PyTorch not available")
    sys.exit(1)

try:
    from diffusers import StableDiffusionXLPipeline
    logger.info("OK Diffusers with SDXL support")
except ImportError as e:
    logger.error(f"FAILED Diffusers error: {e}")
    sys.exit(1)

try:
    import xformers
    logger.info("OK xformers (memory-efficient attention)")
except ImportError:
    logger.warning("WARN xformers not available (fallback to standard attention)")

try:
    import accelerate
    logger.info("OK accelerate package")
except ImportError:
    logger.warning("WARN accelerate not available")

try:
    from services.image_service import ImageService
    logger.info("OK ImageService successfully imported")
    logger.info("\nSUCCESS All optimization packages ready!")
except Exception as e:
    logger.error(f"FAILED ImageService error: {e}")
    sys.exit(1)
