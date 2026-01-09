# -*- coding: utf-8 -*-
import logging
import sys
logging.basicConfig(level=logging.INFO)

import torch
print(f"✅ CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"   Device: {torch.cuda.get_device_name(0)}")
    print(f"   Memory: {torch.cuda.get_device_properties(0).total_memory / (1024**3):.1f}GB")

print("\n🎨 Attempting to load SDXL base model...")

try:
    from diffusers import StableDiffusionXLPipeline
    
    model_id = "stabilityai/stable-diffusion-xl-base-1.0"
    print(f"   Model: {model_id}")
    print(f"   Device: cuda")
    print(f"   Dtype: torch.float32")
    
    pipe = StableDiffusionXLPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float32,
        use_safetensors=True,
        variant=None
    ).to("cuda")
    
    print(f"✅ SDXL base model loaded successfully!")
    
except Exception as e:
    print(f"❌ Error loading SDXL base model:")
    print(f"   {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
