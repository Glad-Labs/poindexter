"""SDXL Image Generation Server — runs on host GPU, serves via HTTP.

Listens on port 9836. Worker calls POST /generate with a prompt to get images.
Uses SDXL Lightning (4-step) for fast generation on RTX 5090.

Usage:
    pythonw scripts/sdxl-server.py     # windowless background
    python scripts/sdxl-server.py      # interactive
"""
import json
import os
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import torch

PORT = 9836
OUTPUT_DIR = Path.home() / "Downloads" / "glad-labs-generated-images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Lazy-load pipeline on first request
_pipeline = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


def _get_pipeline():
    """Load SDXL Lightning pipeline (first call takes ~30s to download/load)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    print(f"Loading SDXL Lightning on {_device}...")
    from diffusers import StableDiffusionXLPipeline, EulerDiscreteScheduler
    from huggingface_hub import hf_hub_download

    # Load base SDXL
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        torch_dtype=torch.float16 if _device == "cuda" else torch.float32,
        variant="fp16" if _device == "cuda" else None,
    )

    # Apply Lightning LoRA for 4-step generation
    pipe.load_lora_weights(
        hf_hub_download(
            "ByteDance/SDXL-Lightning",
            "sdxl_lightning_4step_lora.safetensors",
        )
    )
    pipe.fuse_lora()

    # Use Euler scheduler for Lightning
    pipe.scheduler = EulerDiscreteScheduler.from_config(
        pipe.scheduler.config, timestep_spacing="trailing"
    )

    pipe = pipe.to(_device)

    # Memory optimizations
    if _device == "cuda":
        pipe.enable_attention_slicing()

    _pipeline = pipe
    print(f"SDXL Lightning loaded on {_device} ({torch.cuda.get_device_name(0)})")
    return _pipeline


def _generate(prompt: str, negative_prompt: str = "", steps: int = 4, guidance: float = 1.0) -> str:
    """Generate an image and return the file path."""
    pipe = _get_pipeline()

    default_negative = "blurry, low quality, distorted, watermark, text, ugly, deformed"
    neg = f"{default_negative}, {negative_prompt}" if negative_prompt else default_negative

    with torch.no_grad():
        image = pipe(
            prompt=prompt,
            negative_prompt=neg,
            num_inference_steps=steps,
            guidance_scale=guidance,
            width=1024,
            height=576,  # 16:9 for blog headers
        ).images[0]

    filename = f"{uuid.uuid4().hex[:12]}.png"
    filepath = OUTPUT_DIR / filename
    image.save(str(filepath))
    return str(filepath)


class SDXLHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/generate":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}

            prompt = body.get("prompt", "")
            if not prompt:
                self._respond(400, {"error": "prompt is required"})
                return

            negative = body.get("negative_prompt", "")
            steps = body.get("steps", 4)
            guidance = body.get("guidance_scale", 1.0)

            try:
                start = time.time()
                filepath = _generate(prompt, negative, steps, guidance)
                elapsed = time.time() - start

                # Return image bytes directly
                with open(filepath, "rb") as f:
                    img_data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(img_data)))
                self.send_header("X-Elapsed-Seconds", str(round(elapsed, 2)))
                self.send_header("X-Local-Path", filepath)
                self.end_headers()
                self.wfile.write(img_data)
            except Exception as e:
                self._respond(500, {"error": str(e)})
        else:
            self._respond(200, {"service": "SDXL Lightning", "device": _device, "endpoint": "POST /generate"})

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {
                "status": "ok",
                "model_loaded": _pipeline is not None,
                "device": _device,
                "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A",
            })
        else:
            self._respond(200, {"service": "SDXL Lightning Server", "port": PORT})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Silence request logging


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), SDXLHandler)
    print(f"SDXL Lightning server on :{PORT} (device: {_device})")
    print(f"Output dir: {OUTPUT_DIR}")
    print("Model loads lazily on first /generate request")
    server.serve_forever()
