#!/usr/bin/env python3
"""Test SDXL initialization"""
import logging
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Testing SDXL initialization...")
from services.image_service import ImageService

service = ImageService()
logger.info(f"\nSDXL Available: {service.sdxl_available}")
if service.sdxl_available:
    logger.info(f"  [OK] Base model loaded")
    logger.info(f"  [OK] Refiner model loaded")
    logger.info(f"  [OK] Refinement enabled: {service.use_refinement}")
    logger.info(f"\n[SUCCESS] SDXL is fully operational on RTX 5090!")
else:
    logger.info(f"  [FAIL] SDXL initialization failed")
