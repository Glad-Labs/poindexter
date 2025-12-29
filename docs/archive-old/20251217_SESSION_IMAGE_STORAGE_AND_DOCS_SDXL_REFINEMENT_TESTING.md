# SDXL Refinement - Testing & Verification Guide

## ✅ Implementation Complete

Your SDXL refinement pipeline is now fully integrated and ready to test:

### Files Updated

1. **`src/cofounder_agent/services/image_service.py`**
   - ✅ Added refinement model import
   - ✅ GPU memory detection (fp32 for RTX 5090)
   - ✅ Load both base and refiner models
   - ✅ Two-stage generation pipeline (50 base + 30 refiner steps)
   - ✅ Error handling with fallback to base image

2. **`src/cofounder_agent/routes/media_routes.py`**
   - ✅ Added refinement parameters to API request
   - ✅ Updated endpoint to pass refinement settings
   - ✅ Health check with SDXL status

3. **Documentation**
   - ✅ Created `SDXL_REFINEMENT_GUIDE.md` (comprehensive)

---

## 🚀 Quick Test Steps

### Step 1: Start the Backend

```bash
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python main.py
```

Watch for these logs:

```
✅ Using fp32 (full precision) for best quality
🎨 Loading SDXL base model...
🎨 Loading SDXL refinement model...
✅ SDXL base + refinement models loaded successfully
✅ Refinement: ENABLED
```

### Step 2: Test Health Check

```bash
curl http://localhost:8000/api/media/health
```

Expected response:

```json
{
  "status": "healthy",
  "pexels_available": true,
  "sdxl_available": true,
  "message": "✅ Pexels API available\n✅ SDXL GPU available"
}
```

### Step 3: Generate Image with Refinement

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "futuristic AI agent sitting at holographic desk, cyberpunk office, neon blue lighting, highly detailed, cinematic",
    "use_pexels": false,
    "use_generation": true,
    "use_refinement": true,
    "high_quality": true,
    "num_inference_steps": 50,
    "guidance_scale": 8.0
  }'
```

### Step 4: Monitor Generation

Watch the logs for:

```
🎨 Generating image for prompt: 'futuristic AI agent...'
   Mode: HIGH QUALITY (base steps=50, guidance=8.0)
   Refinement: ENABLED
   ⏱️  Stage 1/2: Base generation (50 steps)...
   ✓ Stage 1 complete: base image latent generated
   ⏱️  Stage 2/2: Refinement pass (30 additional steps)...
   ✓ Stage 2 complete: refinement applied
✅ Image saved to /tmp/generated_image_1704067200.png
```

### Step 5: Verify Image Quality

The generated image should show:

- ✅ Sharp, detailed faces
- ✅ Crisp textures
- ✅ Well-defined background
- ✅ No visible artifacts
- ✅ Professional quality

---

## 🔍 Testing Scenarios

### Scenario 1: High Quality (Production)

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "sleek modern office with AI assistant hologram, volumetric lighting, 4K quality",
    "use_generation": true,
    "use_refinement": true,
    "num_inference_steps": 50,
    "guidance_scale": 8.0
  }'
```

**Expected**: ~30-40 seconds, highest quality

### Scenario 2: Fast Quality

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "sleek modern office with AI assistant hologram",
    "use_generation": true,
    "use_refinement": true,
    "num_inference_steps": 30,
    "guidance_scale": 7.5
  }'
```

**Expected**: ~20-25 seconds, very good quality

### Scenario 3: Without Refinement (Testing)

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "sleek modern office with AI assistant hologram",
    "use_generation": true,
    "use_refinement": false,
    "num_inference_steps": 30
  }'
```

**Expected**: ~12-15 seconds, good but less detailed

### Scenario 4: Fallback to Pexels (if generation disabled)

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "modern office technology",
    "use_pexels": true,
    "use_generation": false
  }'
```

**Expected**: Instant response, stock photo

---

## 📊 Monitoring & Performance

### GPU Monitoring During Generation

```bash
# Real-time GPU monitoring
watch -n 0.5 nvidia-smi --query-gpu=name,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader

# Expected output during refinement:
# NVIDIA RTX 5090, 17000 MiB / 32768 MiB, 55C, 320W
```

### Expected Timing (RTX 5090, fp32)

| Stage           | Duration   | GPU Memory    | GPU Power    |
| --------------- | ---------- | ------------- | ------------ |
| Model Load      | 15-30s     | 15GB          | 100W         |
| Base Generation | 15-20s     | 16GB          | 300W+        |
| Refinement      | 10-15s     | 16GB          | 300W+        |
| **Total**       | **40-65s** | **16GB peak** | **300W avg** |

---

## ✨ Quality Comparison

### Before vs After Refinement

| Aspect          | Base Only    | Base + Refiner      |
| --------------- | ------------ | ------------------- |
| Face Detail     | Good         | Excellent ✨        |
| Hair Texture    | Soft         | Sharp ✨            |
| Eye Detail      | Okay         | Clear ✨            |
| Background      | Basic        | Highly detailed ✨  |
| Artifacts       | Some visible | Minimal ✨          |
| Overall Quality | Good         | Production-ready ✨ |

---

## 🔧 Troubleshooting

### Issue: Refinement Stage Hangs

**Symptom**: Logs show "Stage 2 starting..." but nothing happens

**Solution**:

```bash
# Check GPU memory
nvidia-smi

# If memory is full, restart:
pkill -f "python main.py"
# Wait 10 seconds
python main.py
```

### Issue: "CUDA out of memory" Error

**Unlikely on RTX 5090**, but if it occurs:

```bash
# Reduce steps
use_refinement: false  # Skip refinement
num_inference_steps: 25  # Lower base steps

# Or restart backend
pkill -f "python main.py"
python main.py
```

### Issue: Very Slow Generation (> 1 minute)

**Cause**: GPU thermal throttling (too hot)

**Solution**:

```bash
# Check temperature
nvidia-smi

# Increase fan speed in BIOS or nvidia-settings
# Expected: <65°C during generation

# Reduce load temporarily
num_inference_steps: 30  # Lower from 50
use_refinement: false   # Test without refinement
```

### Issue: API Returns 500 Error

**Check logs**:

```bash
# Look for exceptions
grep -i "error\|exception\|traceback" logs/cofounder_agent.log

# Common causes:
# 1. Out of VRAM (unlikely)
# 2. Model loading failed (check SDXL initialization)
# 3. Latent conversion error (image format issue)
```

---

## ✅ Validation Checklist

Before considering setup complete:

- [ ] Backend starts without SDXL errors
- [ ] Health check shows `sdxl_available: true`
- [ ] Can generate image without refinement (<20s)
- [ ] Can generate image with refinement (30-40s)
- [ ] Generated images show quality improvement
- [ ] GPU memory stays below 25GB
- [ ] GPU temperature stays below 75°C
- [ ] No "out of memory" errors
- [ ] Logs show both Stage 1 and Stage 2 completion

---

## 🎯 Next Steps

1. **Verify Functionality** (use test steps above)
2. **Monitor Performance** (check timing and memory)
3. **Integrate with Oversight Hub** (if not already)
4. **Set Up Image Storage** (save generated images to CDN/S3)
5. **Optimize Settings** (find best quality/speed balance)

---

## 📞 Quick Reference Commands

### Start Backend

```bash
cd src/cofounder_agent && python main.py
```

### Check Health

```bash
curl http://localhost:8000/api/media/health | jq
```

### Generate High Quality

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{"prompt":"futuristic AI","use_generation":true,"use_refinement":true,"num_inference_steps":50}'
```

### Monitor GPU

```bash
nvidia-smi -l 1  # Update every 1 second
```

### Check Logs

```bash
tail -f logs/cofounder_agent.log | grep -i "stage\|generation\|refinement\|error"
```

---

**Everything is ready to test!** 🚀

Start with the quick test steps above. Let me know if you encounter any issues or want to adjust performance settings.
