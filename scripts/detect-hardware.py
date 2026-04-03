#!/usr/bin/env python3
"""
Hardware Detection — auto-detect GPU and recommend Ollama models.

Runs during bootstrap or on-demand. Writes recommendations to app_settings.

Usage:
    python scripts/detect-hardware.py
    python scripts/detect-hardware.py --write-db  # Also save to app_settings
"""

import json
import os
import subprocess
import sys

# Model recommendations by VRAM tier
MODEL_TIERS = {
    "no_gpu": {
        "vram_gb": 0,
        "writer": "ollama/tinyllama:latest",
        "critic": "ollama/tinyllama:latest",
        "seo": "ollama/tinyllama:latest",
        "embed": "nomic-embed-text",
        "note": "CPU-only: expect slow generation, lower quality",
    },
    "low": {
        "vram_gb": 4,
        "writer": "ollama/phi3:latest",
        "critic": "ollama/phi3:latest",
        "seo": "ollama/phi3:latest",
        "embed": "nomic-embed-text",
        "note": "4-6GB VRAM: lightweight models, decent quality",
    },
    "medium": {
        "vram_gb": 8,
        "writer": "ollama/qwen3:8b",
        "critic": "ollama/gemma3:9b",
        "seo": "ollama/qwen3:8b",
        "embed": "nomic-embed-text",
        "note": "8-12GB VRAM: good quality, fast generation",
    },
    "high": {
        "vram_gb": 16,
        "writer": "ollama/qwen3.5:35b",
        "critic": "ollama/gemma3:27b",
        "seo": "ollama/qwen3:8b",
        "embed": "nomic-embed-text",
        "note": "16-24GB VRAM: excellent quality, large models",
    },
    "ultra": {
        "vram_gb": 24,
        "writer": "ollama/qwen3.5:35b",
        "critic": "ollama/glm-4.7-5090:latest",
        "seo": "ollama/qwen3:8b",
        "embed": "nomic-embed-text",
        "note": "24GB+ VRAM: best quality, multi-model QA",
    },
}


def detect_gpu():
    """Detect GPU name and VRAM."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 2:
                return {
                    "name": parts[0].strip(),
                    "vram_mb": int(parts[1].strip()),
                    "vram_gb": round(int(parts[1].strip()) / 1024),
                    "detected": True,
                }
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try ROCm for AMD
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--csv"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and "Total" in result.stdout:
            # Parse ROCm output
            return {"name": "AMD GPU (ROCm)", "vram_mb": 0, "vram_gb": 8, "detected": True}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return {"name": "No GPU detected", "vram_mb": 0, "vram_gb": 0, "detected": False}


def recommend_models(vram_gb):
    """Recommend models based on available VRAM."""
    if vram_gb >= 24:
        return MODEL_TIERS["ultra"]
    elif vram_gb >= 16:
        return MODEL_TIERS["high"]
    elif vram_gb >= 8:
        return MODEL_TIERS["medium"]
    elif vram_gb >= 4:
        return MODEL_TIERS["low"]
    else:
        return MODEL_TIERS["no_gpu"]


def main():
    gpu = detect_gpu()
    tier = recommend_models(gpu["vram_gb"])

    print(f"GPU: {gpu['name']}")
    print(f"VRAM: {gpu['vram_gb']} GB ({gpu['vram_mb']} MB)")
    print(f"Tier: {tier['note']}")
    print()
    print("Recommended models:")
    print(f"  Writer:  {tier['writer']}")
    print(f"  Critic:  {tier['critic']}")
    print(f"  SEO:     {tier['seo']}")
    print(f"  Embed:   {tier['embed']}")

    if "--write-db" in sys.argv:
        try:
            import asyncio
            import asyncpg

            async def write():
                db_url = os.getenv("CLOUD_DATABASE_URL") or os.getenv("DATABASE_URL", "")
                conn = await asyncpg.connect(db_url)
                settings = {
                    "gpu_model": gpu["name"],
                    "gpu_vram_gb": str(gpu["vram_gb"]),
                    "pipeline_writer_model": tier["writer"],
                    "pipeline_critic_model": tier["critic"],
                    "pipeline_seo_model": tier["seo"],
                }
                for key, value in settings.items():
                    await conn.execute(
                        "UPDATE app_settings SET value = $1, updated_at = NOW() WHERE key = $2",
                        value, key,
                    )
                await conn.close()
                print(f"\nWrote {len(settings)} settings to app_settings")

            asyncio.run(write())
        except Exception as e:
            print(f"\nFailed to write to DB: {e}")

    # Output as JSON for scripting
    if "--json" in sys.argv:
        print(json.dumps({"gpu": gpu, "recommendations": tier}, indent=2))


if __name__ == "__main__":
    main()
