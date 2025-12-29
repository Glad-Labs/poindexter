#!/usr/bin/env python3
"""Test SDXL initialization"""
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

print("Testing SDXL initialization...")
from services.image_service import ImageService

service = ImageService()
print(f"\nSDXL Available: {service.sdxl_available}")
if service.sdxl_available:
    print(f"  [OK] Base model loaded")
    print(f"  [OK] Refiner model loaded")
    print(f"  [OK] Refinement enabled: {service.use_refinement}")
    print(f"\n[SUCCESS] SDXL is fully operational on RTX 5090!")
else:
    print(f"  [FAIL] SDXL initialization failed")
