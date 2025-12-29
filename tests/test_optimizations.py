#!/usr/bin/env python3
"""Test SDXL optimization packages"""

import sys
sys.path.insert(0, 'src/cofounder_agent')
import warnings
warnings.filterwarnings('ignore')

print("Checking optimization packages...\n")

try:
    import torch
    print(f"OK PyTorch {torch.__version__}")
except ImportError:
    print("FAILED PyTorch not available")
    sys.exit(1)

try:
    from diffusers import StableDiffusionXLPipeline
    print("OK Diffusers with SDXL support")
except ImportError as e:
    print(f"FAILED Diffusers error: {e}")
    sys.exit(1)

try:
    import xformers
    print("OK xformers (memory-efficient attention)")
except ImportError:
    print("WARN xformers not available (fallback to standard attention)")

try:
    import accelerate
    print("OK accelerate package")
except ImportError:
    print("WARN accelerate not available")

try:
    from services.image_service import ImageService
    print("OK ImageService successfully imported")
    print("\nSUCCESS All optimization packages ready!")
except Exception as e:
    print(f"FAILED ImageService error: {e}")
    sys.exit(1)
