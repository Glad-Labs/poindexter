# -*- coding: utf-8 -*-
import logging
import sys
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import torch
logger.info(f"✅ CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    logger.info(f"   Device: {torch.cuda.get_device_name(0)}")
    logger.info(f"   Memory: {torch.cuda.get_device_properties(0).total_memory / (1024**3):.1f}GB")

logger.info("\n🎨 Attempting to load SDXL base model...")

try:
    from diffusers import StableDiffusionXLPipeline
    
    model_id = "stabilityai/stable-diffusion-xl-base-1.0"
    logger.info(f"   Model: {model_id}")
    logger.info(f"   Device: cuda")
    logger.info(f"   Dtype: torch.float32")
    
    pipe = StableDiffusionXLPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float32,
        use_safetensors=True,
        variant=None
    ).to("cuda")
    
    logger.info(f"✅ SDXL base model loaded successfully!")
    
except Exception as e:
    logger.error(f"❌ Error loading SDXL base model:")
    logger.error(f"   {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
