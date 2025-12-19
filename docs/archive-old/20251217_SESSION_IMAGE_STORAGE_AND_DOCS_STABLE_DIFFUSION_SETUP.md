# Stable Diffusion XL (SDXL) Setup Guide for Windows

## Overview

This guide sets up SDXL locally on your Windows PC to integrate with your FastAPI image generation endpoint.

## Step 1: Install CUDA (GPU Support)

### Check Your GPU

```powershell
# Open PowerShell as Administrator and run:
nvidia-smi
```

If you see GPU info, you have an NVIDIA GPU. If not, SDXL won't work (fall back to Pexels).

### Install CUDA Toolkit

1. Download from: https://developer.nvidia.com/cuda-downloads
2. Select: Windows → x86_64 → Windows 10/11 → exe (network)
3. Run installer and follow prompts
4. Choose "Custom Installation" and ensure:
   - CUDA Toolkit ✓
   - cuDNN ✓
   - Visual Studio Integration ✓

### Verify Installation

```powershell
nvcc --version
```

## Step 2: Install PyTorch with CUDA Support

```bash
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent

# Activate your Python environment (if using venv)
python -m venv sdxl_env
.\sdxl_env\Scripts\activate

# Install PyTorch with CUDA support
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Verify PyTorch can see GPU
python -c "import torch; print(torch.cuda.is_available())"
# Should print: True
```

## Step 3: Install Diffusers Library

```bash
# Install the Hugging Face diffusers library
pip install diffusers transformers accelerate safetensors

# For faster inference
pip install xformers  # Optional but recommended for speed
```

## Step 4: Download SDXL Model (5GB)

The first time SDXL runs, it automatically downloads the model (~5GB). This can take 10-30 minutes.

**Alternative: Pre-download the model**

```bash
python << 'EOF'
from diffusers import StableDiffusionXLPipeline
import torch

print("⏳ Downloading SDXL model (this may take 10-30 minutes)...")

pipeline = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16,
    use_safetensors=True,
    variant="fp16"
)

print("✅ Model downloaded and cached!")
EOF
```

## Step 5: Update Your FastAPI Image Service

The `image_service.py` in your codebase should already have SDXL support. Let's verify it's configured correctly:

### Check Current Configuration

File: `src/cofounder_agent/services/image_service.py`

The service should:

1. ✅ Auto-detect GPU availability
2. ✅ Load SDXL model on first use
3. ✅ Generate images in temp directory
4. ✅ Return base64-encoded images

### Key Settings to Verify

```python
# In image_service.py, around line 150-200:

# GPU detection
self.device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"🖥️ Device: {self.device}")

# Model loading
if use_gpu:
    self.pipeline = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        torch_dtype=torch.float16,
        use_safetensors=True,
    ).to("cuda")
```

## Step 6: Test SDXL Locally

```bash
# Test image generation
python << 'EOF'
import sys
sys.path.insert(0, 'src/cofounder_agent')

from services.image_service import ImageService
import asyncio

async def test_sdxl():
    service = ImageService()
    await service.initialize()

    # Generate a test image
    success = await service.generate_image(
        prompt="A beautiful mountain landscape at sunset",
        output_path="test_image.png"
    )

    if success:
        print("✅ Image generated: test_image.png")
    else:
        print("❌ Image generation failed")

asyncio.run(test_sdxl())
EOF
```

## Step 7: Test Through FastAPI

```bash
# Start FastAPI backend
cd src/cofounder_agent
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# In another terminal, test the endpoint
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A beautiful mountain landscape at sunset",
    "title": "Mountain",
    "use_pexels": false,
    "use_generation": true
  }'
```

**First run will:**

- Download model (~5GB) ← Takes 10-30 minutes
- Generate image ← Takes 15-30 seconds

**Subsequent runs:**

- Just generate image ← Takes 15-30 seconds

## Step 8: Integrate with Oversight Hub

Once working:

1. Open Oversight Hub at http://localhost:3000
2. Create/open a blog post task
3. Select image source: **🎨 SDXL (GPU-based)**
4. Click "Generate" button
5. Wait 15-30 seconds for image to generate

## Troubleshooting

### ❌ "CUDA not available" / `torch.cuda.is_available() = False`

**Solution:**

```bash
# Reinstall PyTorch with correct CUDA version
pip uninstall torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### ❌ "Out of Memory" (OOM) Error

**Solutions:**

1. Use smaller batch size (already set to 1)
2. Use fp16 (half precision) - already enabled
3. Use memory-efficient attention: `pip install xformers`
4. Close other GPU applications (browsers, games, etc.)

### ❌ "Model not found" / 404 Error

**Solution:**

```bash
# Manually download model
python << 'EOF'
from diffusers import StableDiffusionXLPipeline
import torch

StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16,
    use_safetensors=True,
    variant="fp16"
)
EOF
```

### ❌ Slow Performance

**Optimizations:**

```bash
# Install optional speed improvements
pip install xformers triton

# Update image_service.py to use optimizations:
# Add: enable_attention_slicing(1)
# Add: to("cuda") after model loading
```

### ❌ "RuntimeError: CUDA out of memory"

**Quick Fix - Reduce Image Dimension:**
In `ResultPreviewPanel.jsx`, change resolution default:

```javascript
resolution: '512x512',  // Changed from 1024x1024
```

## Performance Expectations

### RTX 3060 (12GB VRAM)

- First generation: 30-45 seconds
- Subsequent: 20-30 seconds

### RTX 4090 (24GB VRAM)

- First generation: 15-20 seconds
- Subsequent: 8-12 seconds

### RTX 2060 (6GB VRAM)

- May fail or be very slow
- Use Pexels instead (instant, free)

## Cost Comparison

| Source             | Cost                  | Speed  | Quality      | Unlimited |
| ------------------ | --------------------- | ------ | ------------ | --------- |
| **Pexels**         | FREE                  | <1s    | Stock Photos | ✅        |
| **SDXL Local GPU** | FREE (one-time setup) | 15-45s | AI Generated | ✅        |
| **DALL-E**         | $0.02/image           | 10-30s | Best         | ❌        |
| **Midjourney**     | $10-60/month          | 30-60s | Best         | ✅        |

## Recommended Strategy

1. **Default: Use "Try Both"** → Tries Pexels first (instant), falls back to SDXL if no matches
2. **For urgent content**: Use Pexels only (always works)
3. **For creative content**: Use SDXL (when GPU available)
4. **For production**: Set up Pexels API key as fallback

## Final Configuration

Update `.env.local`:

```bash
# Optional Pexels fallback
PEXELS_API_KEY=your_api_key_here

# SDXL settings (auto-detected)
ENABLE_GPU=true
SDXL_MODEL=stabilityai/stable-diffusion-xl-base-1.0
```

## Next Steps

1. ✅ Check if you have GPU: `nvidia-smi`
2. ✅ Install CUDA if available
3. ✅ Install PyTorch with CUDA
4. ✅ Install diffusers library
5. ✅ Pre-download model (optional, happens auto on first use)
6. ✅ Test locally
7. ✅ Test through FastAPI
8. ✅ Use in Oversight Hub

Good luck! 🚀
