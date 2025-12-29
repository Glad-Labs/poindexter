# SDXL Refinement Pipeline Guide

## 🎯 Overview

Your RTX 5090 (32GB VRAM) can run **production-quality** SDXL images with a two-stage refinement pipeline:

1. **Base Model** (50 steps): Generates high-quality base image with detailed composition
2. **Refiner Model** (30 steps): Applies additional detail refinement for professional results

**Result**: ~30-40 second generation time with **significantly better image quality** than single-stage

---

## 📊 Architecture

### Two-Stage Pipeline

```
Prompt
  ↓
[Stage 1] SDXL Base Model (50 steps)
  ├─ Input: prompt, negative prompt, guidance scale 8.0
  ├─ Output: Latent representation (NOT image yet)
  └─ Time: ~15-20 seconds on RTX 5090
  ↓
[Stage 2] SDXL Refiner Model (30 steps)
  ├─ Input: Latent from Stage 1
  ├─ Output: Final PIL Image
  └─ Time: ~10-15 seconds on RTX 5090
  ↓
Save to disk
```

### Hardware Detection

The image service **automatically** detects your GPU and selects optimal precision:

```python
GPU Memory ≥ 20GB  →  Use fp32 (Full Precision)     ← RTX 5090 uses this! ✨
GPU Memory < 20GB  →  Use fp16 (Half Precision)     ← Memory-efficient fallback
```

**RTX 5090 Advantage**: 32GB VRAM allows fp32 precision = better image quality (no rounding)

---

## 🚀 Quick Start

### 1. Verify Setup

Check that SDXL models are loaded with refinement:

```bash
# Check logs during startup
grep "SDXL\|Refinement" logs/cofounder_agent.log

# Expected output:
# ✅ Using fp32 (full precision) for best quality
# 🎨 Loading SDXL base model...
# 🎨 Loading SDXL refinement model...
# ✅ SDXL base + refinement models loaded successfully
# ✅ Refinement: ENABLED
```

### 2. Generate Image via API

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "futuristic AI agent sitting at holographic desk, cyberpunk, neon lights, highly detailed",
    "use_pexels": false,
    "use_generation": true,
    "use_refinement": true,
    "high_quality": true,
    "num_inference_steps": 50,
    "guidance_scale": 8.0
  }'
```

### 3. Expected Output

```json
{
  "success": true,
  "image_url": "/tmp/generated_image_1704067200.png",
  "image": {
    "url": "/tmp/generated_image_1704067200.png",
    "source": "sdxl",
    "photographer": null,
    "photographer_url": null,
    "width": 1024,
    "height": 1024
  },
  "message": "Image generated successfully",
  "generation_time": 32.5
}
```

---

## 📈 Performance Characteristics

### Timing Breakdown (RTX 5090, fp32)

| Stage           | Steps  | Time       | Notes                        |
| --------------- | ------ | ---------- | ---------------------------- |
| Model Load      | N/A    | 15-30s     | First time only, then cached |
| Base Generation | 50     | 15-20s     | Main image composition       |
| Refinement      | 30     | 10-15s     | High-detail pass             |
| **Total**       | **80** | **25-35s** | From prompt to saved image   |

### Quality Levels

#### High Quality (Recommended for RTX 5090)

```json
{
  "use_refinement": true,
  "high_quality": true,
  "num_inference_steps": 50,
  "guidance_scale": 8.0
}
```

- **Time**: 30-40 seconds
- **Quality**: Production-ready, highly detailed, minimal artifacts
- **Use Case**: Featured images for blog posts, marketing, professional content

#### Medium Quality (Fast, Still Good)

```json
{
  "use_refinement": true,
  "high_quality": true,
  "num_inference_steps": 30,
  "guidance_scale": 7.5
}
```

- **Time**: 18-25 seconds
- **Quality**: Very good, slight less detail than high quality
- **Use Case**: Gallery images, quick generation iterations

#### Fast Quality (Base Only, No Refinement)

```json
{
  "use_refinement": false,
  "high_quality": false,
  "num_inference_steps": 20,
  "guidance_scale": 7.5
}
```

- **Time**: 8-12 seconds
- **Quality**: Good but visible artifacts in fine details
- **Use Case**: Previews, experiments, testing

---

## 🎨 API Parameters

### Generation Parameters

```typescript
interface ImageGenerationRequest {
  // Required
  prompt: string; // Image description (max 500 chars)

  // Search options
  use_pexels: boolean; // Default: true (try free stock images first)
  use_generation: boolean; // Default: false (generate with SDXL)

  // Generation quality
  use_refinement: boolean; // Default: true (enable refinement model)
  high_quality: boolean; // Default: true (optimize for quality)
  num_inference_steps: int; // Default: 50 (20-100, higher = better quality)
  guidance_scale: float; // Default: 8.0 (1.0-20.0, higher = prompt adherence)
}
```

### Recommended Configurations

**Best Quality (Production)**

```json
{
  "prompt": "your image description",
  "use_generation": true,
  "use_refinement": true,
  "num_inference_steps": 50,
  "guidance_scale": 8.0
}
```

**Fast & Good (Balanced)**

```json
{
  "prompt": "your image description",
  "use_generation": true,
  "use_refinement": true,
  "num_inference_steps": 35,
  "guidance_scale": 7.5
}
```

**Experimental (Quick Testing)**

```json
{
  "prompt": "your image description",
  "use_generation": true,
  "use_refinement": false,
  "num_inference_steps": 25,
  "guidance_scale": 7.0
}
```

---

## 💾 Memory Management

### GPU Memory During Generation

```
Phase 1: Model Loading (~30-45 seconds)
├─ SDXL Base: 6.9GB
├─ SDXL Refiner: 6.7GB
├─ Working Memory: 2-3GB
└─ Total: ~15-17GB out of 32GB ✅ SAFE

Phase 2: Base Generation (running)
├─ Models: 13.6GB
├─ Latent Tensor: 0.5GB
├─ Intermediate: 1-2GB
└─ Total: ~15-17GB ✅ SAFE

Phase 3: Refinement (running)
├─ Models: 13.6GB
├─ Latent Tensor: 0.5GB
├─ Intermediate: 1-2GB
└─ Total: ~15-17GB ✅ SAFE

Peak Usage: ~17GB out of 32GB
Utilization: 53% (very safe)
```

### Memory Optimization Tips

1. **Run single generation at a time** (no parallel requests)
2. **Monitor temperature**: RTX 5090 throttles at ~80°C
3. **Ensure adequate cooling**: Keep case fans at 60%+ speed
4. **Check available VRAM**: `nvidia-smi` before generation

```bash
# Monitor GPU during generation
watch -n 1 nvidia-smi

# Expected output:
# NVIDIA-SMI 555.58
# GPU  Name    TmpC  PwrDraw  Memory-Usage
# 0    RTX 5090  55C  320W     17000MiB / 32768MiB
```

---

## 🔧 Configuration & Customization

### Enable/Disable Refinement

**In Service**: `src/cofounder_agent/services/image_service.py`

```python
self.use_refinement = True  # Line 109: Toggle here for all generations
```

**Per-Request**: Use API `use_refinement` parameter

```bash
curl ... -d '{"use_refinement": false}'  # Skip refinement
```

### Change Inference Steps

**For More Detail** (slower):

```json
{
  "num_inference_steps": 60,
  "use_refinement": true
}
```

→ 40 base + 30 refiner = ~45 seconds, very high detail

**For Faster Generation** (less detail):

```json
{
  "num_inference_steps": 30,
  "use_refinement": false
}
```

→ 30 base only = ~15 seconds, good balance

### Adjust Guidance Scale

**For More Prompt Adherence** (can be over-saturated):

```json
{ "guidance_scale": 9.0 }
```

**For More Creativity** (can ignore prompt):

```json
{ "guidance_scale": 7.0 }
```

**Sweet Spot for Most Prompts**:

```json
{"guidance_scale": 8.0}  # Default
```

---

## 📋 Model Information

### SDXL Base Model

- **Name**: `stabilityai/stable-diffusion-xl-base-1.0`
- **Size**: 6.9GB (fp32) / 3.5GB (fp16)
- **Resolution**: 1024×1024 pixels
- **Training Data**: Multi-billion image dataset
- **Purpose**: High-quality image generation with detailed composition

### SDXL Refiner Model

- **Name**: `stabilityai/stable-diffusion-xl-refiner-1.0`
- **Size**: 6.7GB (fp32) / 3.5GB (fp16)
- **Resolution**: 1024×1024 pixels
- **Purpose**: Refinement of base output, sharper details, reduced artifacts
- **Input**: Latent representation from base model
- **Output**: Final RGB image

### Why Two Models?

| Aspect           | Base                 | Refiner             |
| ---------------- | -------------------- | ------------------- |
| **Purpose**      | Composition, objects | Details, refinement |
| **Architecture** | Full UNet            | Optimized UNet      |
| **Strengths**    | Good structure       | Sharp details       |
| **Use Alone**    | Good results         | Only polish         |
| **Use Together** | Production quality   | Best results        |

---

## 🐛 Troubleshooting

### Issue: "No image found. Ensure PEXELS_API_KEY is set or GPU available"

**Cause**: SDXL not initialized (GPU missing or error during load)

**Solution**:

```bash
# 1. Check CUDA availability
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# 2. Check GPU memory
nvidia-smi

# 3. Check logs for SDXL errors
grep -i "sdxl\|cuda\|error" logs/cofounder_agent.log

# 4. Verify diffusers installed
pip list | grep diffusers
```

### Issue: Refinement Stage Fails, Uses Base Image

**Cause**: Refiner model failed (OOM, incompatible latents, etc.)

**Solution**:

```bash
# Check logs for refinement errors
grep -i "refinement failed" logs/cofounder_agent.log

# If refiner crashes:
# 1. Set use_refinement=false to skip refinement
# 2. Reduce num_inference_steps to 30
# 3. Check GPU temperature (might be throttling)
```

### Issue: Very Slow Generation (40+ seconds)

**Cause**: CPU bottleneck or GPU thermal throttling

**Solution**:

```bash
# 1. Monitor GPU during generation
nvidia-smi

# 2. Check temperature
# If temp > 80°C: Improve cooling, increase fan speed

# 3. Check if CPU is saturated
# If yes: Close other applications

# 4. Use fewer steps (temporary)
# num_inference_steps: 35 instead of 50
```

### Issue: "Out of Memory" Error

**Cause**: Unlikely on RTX 5090, but can happen if:

- GPU memory driver issue
- Memory leak from previous generation
- System RAM low (affects CUDA)

**Solution**:

```bash
# 1. Restart Python process
pkill python

# 2. Check system RAM
free -h  # Linux/Mac
wmic OS get TotalVisibleMemorySize,FreePhysicalMemory  # Windows

# 3. If persistent: Reduce num_inference_steps or disable refinement
```

---

## 📊 Comparison: Refinement Impact

### Before Refinement (Base Only, 30 steps)

```
✗ Faces can be blurry
✗ Fine details soft
✗ Texture less crisp
✗ Background less detailed
- Time: 12-15 seconds
+ Lower VRAM (by ~2GB)
```

### After Refinement (Base 50 + Refine 30 steps)

```
✓ Faces sharp and detailed
✓ Fine details crisp
✓ Textures well-defined
✓ Background highly detailed
✓ Professional-quality output
- Time: 30-40 seconds (worth it!)
- Uses more VRAM (but RTX 5090 handles it)
```

---

## 🎯 Best Practices

### Prompt Engineering

**Good Prompts** (work well with SDXL):

```
"a futuristic AI agent sitting at a holographic desk,
cyberpunk office, neon blue and purple lighting,
highly detailed, cinematic, 4K, volumetric lighting"
```

**Bad Prompts** (vague, confusing):

```
"AI robot thing"
"some future stuff"
```

### Negative Prompts

Help avoid common artifacts:

```json
{
  "prompt": "your positive prompt",
  "negative_prompt": "blurry, low quality, distorted, ugly, bad anatomy,
                      oversaturated, watermark, text, logo"
}
```

### Generation Workflow

1. **Try Pexels First** (Free, instant)

   ```bash
   use_pexels: true, use_generation: false
   ```

2. **If No Match, Generate** (Takes 30-40s)

   ```bash
   use_pexels: false, use_generation: true, use_refinement: true
   ```

3. **If Happy with Result** (Save for reuse)
   - Store generated image in CDN or database
   - Track prompt that created it

4. **Iterate if Needed** (Try different guidance, steps)
   - Increase guidance_scale if too creative
   - Increase num_inference_steps if missing details

---

## 📚 References

- **SDXL Paper**: https://arxiv.org/abs/2307.01952
- **Stability AI Models**: https://huggingface.co/stabilityai
- **Diffusers Library**: https://huggingface.co/docs/diffusers
- **RTX 5090 Specs**: https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/

---

## ✨ Summary

Your RTX 5090 setup can:

- ✅ Run fp32 precision (better quality than fp16)
- ✅ Use SDXL base + refiner models simultaneously
- ✅ Generate 1024×1024 production-quality images
- ✅ Process in 30-40 seconds with refinement
- ✅ Handle memory-intensive operations safely
- ✅ Fallback gracefully if GPU unavailable

**Next Step**: Start generating! Use the API examples above to create your first refined image. 🎨
