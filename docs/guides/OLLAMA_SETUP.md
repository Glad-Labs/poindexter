# Ollama Setup Guide - Zero-Cost Local AI Inference

**GLAD LABS AI Frontier Firm**  
**Date**: October 15, 2025  
**Status**: Production Ready ✅

---

## 🎯 Overview

This guide shows you how to run the GLAD LABS platform with **$0.00 API costs** using Ollama for local AI model inference on your desktop.

### Why Ollama?

| Feature     | OpenAI/Claude (Cloud) | Ollama (Local)    |
| ----------- | --------------------- | ----------------- |
| **Cost**    | $10-30 per 1M tokens  | **$0.00**         |
| **Privacy** | Data sent to cloud    | **100% local**    |
| **Speed**   | Network latency       | **Direct GPU**    |
| **Offline** | Requires internet     | **Works offline** |
| **Models**  | Limited selection     | **10+ models**    |

### System Requirements

**Minimum**:

- CPU: 4+ cores
- RAM: 8GB
- Disk: 10GB free space
- OS: Windows 10/11, macOS 11+, Linux

**Recommended**:

- CPU: 8+ cores
- RAM: 16GB+
- GPU: NVIDIA/AMD with 8GB+ VRAM
- Disk: 50GB SSD

---

## 📦 Installation

### Windows

```powershell
# Download installer from https://ollama.ai/download
# Or use winget
winget install Ollama.Ollama

# Verify installation
ollama --version
```

### macOS

```bash
# Download from https://ollama.ai/download
# Or use Homebrew
brew install ollama

# Verify installation
ollama --version
```

### Linux

```bash
# Install via curl
curl -fsSL https://ollama.ai/install.sh | sh

# Verify installation
ollama --version
```

### Start Ollama Service

```bash
# Ollama runs as a background service
# It starts automatically on installation

# Check status
ollama list

# Should show: "Ollama is running"
```

---

## 🤖 Model Selection Guide

### Model Comparison

| Model          | Size | Speed      | Quality    | Best For                     | RAM Needed |
| -------------- | ---- | ---------- | ---------- | ---------------------------- | ---------- |
| **phi**        | 2.7B | ⚡⚡⚡⚡⚡ | ⭐⭐⭐     | Simple tasks, classification | 4GB        |
| **mistral**    | 7B   | ⚡⚡⚡⚡   | ⭐⭐⭐⭐   | General purpose, chat        | 8GB        |
| **llama2**     | 7B   | ⚡⚡⚡⚡   | ⭐⭐⭐     | Chat, Q&A                    | 8GB        |
| **codellama**  | 7B   | ⚡⚡⚡⚡   | ⭐⭐⭐⭐   | Code generation, review      | 8GB        |
| **llama2:13b** | 13B  | ⚡⚡⚡     | ⭐⭐⭐⭐⭐ | Complex reasoning            | 16GB       |
| **mixtral**    | 8x7B | ⚡⚡⚡     | ⭐⭐⭐⭐⭐ | Expert-level analysis        | 32GB       |
| **llama2:70b** | 70B  | ⚡         | ⭐⭐⭐⭐⭐ | Critical tasks only          | 64GB       |

### Recommended Models by Use Case

#### 💼 Business Use (Recommended: **mistral**)

```bash
ollama pull mistral
```

- **Best balance** of speed and quality
- Excellent for: emails, summaries, reports
- Fast enough for real-time interaction
- **Most popular choice**

#### 💻 Development (Recommended: **codellama**)

```bash
ollama pull codellama
```

- Specialized for code generation
- Best for: debugging, code review, refactoring
- Understands 10+ programming languages

#### ⚡ Quick Tasks (Recommended: **phi**)

```bash
ollama pull phi
```

- **Blazing fast** responses
- Good for: classification, extraction, simple Q&A
- Runs on minimal hardware

#### 🧠 Complex Analysis (Recommended: **mixtral**)

```bash
ollama pull mixtral
```

- **Outstanding reasoning** capabilities
- Best for: research, analysis, complex problems
- Requires beefy hardware (32GB RAM)

#### 🏆 Maximum Quality (Use: **llama2:70b**)

```bash
ollama pull llama2:70b
```

- **Top-tier quality** for critical tasks
- Best for: legal review, financial analysis
- Requires high-end workstation (64GB RAM)

---

## ⚙️ GLAD LABS Configuration

### 1. Enable Ollama in Your Environment

**Windows (PowerShell)**:

```powershell
# Set environment variable
$env:USE_OLLAMA = "true"

# Make it permanent
[System.Environment]::SetEnvironmentVariable("USE_OLLAMA", "true", "User")
```

**macOS/Linux (Bash)**:

```bash
# Add to ~/.bashrc or ~/.zshrc
export USE_OLLAMA=true

# Reload shell
source ~/.bashrc
```

### 2. Pull Your Preferred Model

```bash
# Recommended for most users
ollama pull mistral

# Verify download
ollama list
# Should show: mistral
```

### 3. Test Ollama Connection

```bash
# Quick test
ollama run mistral "Say hello"
# Should respond: "Hello! How can I help you today?"

# Exit interactive mode
/bye
```

### 4. Start GLAD LABS Platform

```bash
# Navigate to project
cd C:\Users\mattm\glad-labs-website

# Ollama will be automatically used!
npm run dev
```

### 5. Verify Zero-Cost Operation

Check the Co-Founder Agent logs:

```
✅ Ollama client initialized
✅ Using Ollama for zero-cost local inference
✅ Model: mistral
✅ Cost: $0.00
```

---

## 🚀 Performance Optimization

### GPU Acceleration

Ollama automatically uses your GPU if available:

**NVIDIA GPU**:

- Requires CUDA 11.7+
- Automatically detected
- 5-10x faster than CPU

**AMD GPU**:

- Requires ROCm 5.4+
- Configure with `OLLAMA_GPU=rocm`

**Apple Silicon (M1/M2/M3)**:

- Uses Metal acceleration
- Excellent performance out-of-box

### Memory Management

```bash
# Check model memory usage
ollama ps

# Unload models to free memory
ollama stop <model-name>

# Load specific model
ollama load <model-name>
```

### Concurrent Requests

Ollama handles multiple requests efficiently:

- **CPU**: 2-4 concurrent requests
- **GPU (8GB)**: 4-8 concurrent requests
- **GPU (16GB+)**: 8-16 concurrent requests

### Benchmarks (on typical hardware)

**Intel i7 + NVIDIA RTX 3060 (12GB)**:
| Model | Tokens/sec | First Response | Use Case |
|-------|-----------|----------------|----------|
| phi | 150-200 | < 0.5s | Interactive chat |
| mistral | 80-120 | < 1s | General purpose |
| codellama | 70-100 | < 1s | Code generation |
| mixtral | 30-50 | 1-2s | Complex reasoning |

**Apple M2 Max (64GB)**:
| Model | Tokens/sec | First Response | Use Case |
|-------|-----------|----------------|----------|
| phi | 180-250 | < 0.3s | Interactive chat |
| mistral | 100-150 | < 0.5s | General purpose |
| llama2:13b | 60-90 | < 1s | Analysis |
| mixtral | 40-70 | 1s | Expert reasoning |

---

## 🔧 Troubleshooting

### Issue: "Ollama not found"

**Solution**:

```bash
# Check if Ollama is installed
ollama --version

# If not installed, download from:
# https://ollama.ai/download
```

### Issue: "Connection refused"

**Solution**:

```bash
# Check if Ollama service is running
curl http://localhost:11434

# Restart Ollama
# Windows: Restart from system tray
# macOS: brew services restart ollama
# Linux: systemctl restart ollama
```

### Issue: "Model not found"

**Solution**:

```bash
# List installed models
ollama list

# Pull the missing model
ollama pull mistral

# Verify
ollama list
```

### Issue: "Out of memory"

**Solution**:

```bash
# Check current memory usage
ollama ps

# Stop running models
ollama stop mistral

# Use a smaller model
ollama pull phi  # Only 2.7B parameters
```

### Issue: "Slow generation"

**Causes & Solutions**:

1. **CPU-only inference**:
   - Solution: Install CUDA/ROCm for GPU
   - Or: Use smaller model (phi, mistral)

2. **Too many concurrent requests**:
   - Solution: Limit concurrent users
   - Or: Upgrade RAM/GPU

3. **Large model on small hardware**:
   - Solution: Use mistral (7B) instead of mixtral (8x7B)

### Issue: "GLAD LABS not using Ollama"

**Checklist**:

```bash
# 1. Check environment variable
echo $env:USE_OLLAMA  # Windows
echo $USE_OLLAMA      # macOS/Linux
# Should output: true

# 2. Check Ollama is running
curl http://localhost:11434
# Should return: Ollama is running

# 3. Check model is installed
ollama list
# Should show your model

# 4. Check logs
# Look for: "Ollama client initialized"
```

---

## 🔄 Switching Between Cloud and Local

### Use Ollama (Local, Free):

```powershell
$env:USE_OLLAMA = "true"
npm run dev
# Cost: $0.00/month ✅
```

### Use OpenAI/Claude (Cloud, Paid):

```powershell
$env:USE_OLLAMA = "false"
npm run dev
# Cost: ~$10-30/month 💰
```

### Hybrid Mode (Best of Both):

```python
# In model_router.py configuration:
# - Use Ollama for non-critical tasks (80% of requests)
# - Use GPT-4 for critical tasks (20% of requests)
# - Total cost: ~$2-5/month ✅
```

---

## 📊 Cost Comparison

### Monthly Usage: 1M tokens

| Provider   | Model         | Cost      | Savings vs GPT-4     |
| ---------- | ------------- | --------- | -------------------- |
| OpenAI     | GPT-4 Turbo   | $30.00    | -                    |
| OpenAI     | GPT-3.5 Turbo | $2.00     | $28.00 (93%)         |
| Anthropic  | Claude Sonnet | $15.00    | $15.00 (50%)         |
| **Ollama** | **mistral**   | **$0.00** | **$30.00 (100%)** ✅ |
| **Ollama** | **mixtral**   | **$0.00** | **$30.00 (100%)** ✅ |

### Annual Savings

| Scenario                      | Cloud Cost | Ollama Cost | Annual Savings |
| ----------------------------- | ---------- | ----------- | -------------- |
| Light use (100K tokens/month) | $120       | **$0**      | **$120**       |
| Medium use (1M tokens/month)  | $1,200     | **$0**      | **$1,200**     |
| Heavy use (10M tokens/month)  | $12,000    | **$0**      | **$12,000**    |

**ROI on Hardware**: If you buy a $2,000 GPU for Ollama, it pays for itself in ~2 months of heavy usage!

---

## 🔒 Privacy & Security

### Data Privacy

**Cloud APIs (OpenAI/Claude)**:

- ❌ Data sent to third-party servers
- ❌ Subject to their privacy policies
- ❌ Potential for data retention
- ❌ Requires internet connection

**Ollama (Local)**:

- ✅ **100% local processing**
- ✅ **No data leaves your machine**
- ✅ **No internet required after model download**
- ✅ **Full control over your data**

### Use Cases Requiring Privacy

- 🏥 Healthcare data (HIPAA compliance)
- 💼 Confidential business plans
- ⚖️ Legal documents
- 💰 Financial records
- 🔐 Trade secrets

**Recommendation**: Use Ollama for any sensitive data processing.

---

## 🎓 Best Practices

### 1. Start with Mistral

Most users should start with `mistral`:

- Excellent quality/speed balance
- Runs on most hardware
- Handles 80% of tasks well

### 2. Keep Multiple Models

```bash
# Install a small, medium, and large model
ollama pull phi      # Fast fallback
ollama pull mistral  # Default
ollama pull mixtral  # Complex tasks
```

### 3. Monitor Resource Usage

```bash
# Check what's running
ollama ps

# Monitor system resources
# Windows: Task Manager
# macOS: Activity Monitor
# Linux: htop
```

### 4. Preload Models

```bash
# Preload before heavy usage to avoid first-load delay
ollama load mistral
```

### 5. Update Regularly

```bash
# Check for updates
ollama pull mistral

# Ollama notifies when updates available
```

---

## 🆘 Support & Resources

### Official Resources

- **Ollama Website**: https://ollama.ai
- **Model Library**: https://ollama.ai/library
- **GitHub**: https://github.com/ollama/ollama
- **Discord**: https://discord.gg/ollama

### GLAD LABS Resources

- **Documentation**: `/docs/ARCHITECTURE.md`
- **Developer Guide**: `/docs/DEVELOPER_GUIDE.md`
- **Cost Dashboard**: http://localhost:3001/oversight (see real-time savings!)

### Community

- Report issues: GitHub Issues
- Questions: GitHub Discussions
- Updates: Watch the repo

---

## 🚀 Quick Start Checklist

- [ ] Install Ollama
- [ ] Pull a model (`ollama pull mistral`)
- [ ] Set `USE_OLLAMA=true` environment variable
- [ ] Start GLAD LABS platform
- [ ] Verify zero-cost operation in logs
- [ ] Monitor performance in Cost Dashboard
- [ ] Enjoy **$0.00/month AI costs!** 🎉

---

## 📈 Next Steps

1. **Test your setup**: Try a simple chat to verify everything works
2. **Benchmark performance**: Run a few tasks and check response times
3. **Monitor savings**: Check the Cost Dashboard daily to see cumulative savings
4. **Optimize**: Adjust models based on your actual usage patterns
5. **Scale**: Add more models as needed for different task types

---

**Questions?** Check the troubleshooting section or open a GitHub issue.

**Status**: ✅ Ollama support is production-ready and battle-tested!

**Last Updated**: October 15, 2025
