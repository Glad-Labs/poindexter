"""
SDXL Image Generation Server - standalone HTTP service on the host GPU.

One service, one GPU, unlimited callers.
Runs on host machine with direct RTX 5090 access.
Docker containers call via host.docker.internal:9836.

Usage:
    python scripts/sdxl-server.py
    pythonw scripts/sdxl-server.py

API:
    POST /generate  - generate image from prompt
    GET  /health    - check GPU status
    GET  /images/{filename} - serve generated image
"""
import argparse, logging, os, time, uuid
from pathlib import Path
import torch, uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

OUTPUT_DIR = Path(os.path.expanduser("~")) / ".gladlabs" / "generated-images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_MODEL = "stabilityai/sdxl-turbo"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sdxl-server")
app = FastAPI(title="SDXL Image Server", version="1.0")
_pipeline = None
_model_name = None
_last_used = 0.0
IDLE_TIMEOUT = 60  # 1 minute — unload fast so Ollama can use VRAM for next pipeline step

class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = Field(default="text, words, letters, watermark, face, person, hands, blurry, low quality, deformed")
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=4, ge=1, le=50)
    guidance_scale: float = Field(default=0.0, ge=0, le=20)
    seed: int = Field(default=-1)

class GenerateResponse(BaseModel):
    image_path: str
    filename: str
    width: int
    height: int
    model: str
    generation_time_ms: int
    seed: int

def _unload_pipeline():
    """Free VRAM by unloading the SDXL model."""
    global _pipeline, _model_name
    if _pipeline is None:
        return
    logger.info("Unloading SDXL model to free VRAM (idle timeout)")
    del _pipeline
    _pipeline = None
    _model_name = None
    torch.cuda.empty_cache()
    import gc
    gc.collect()
    logger.info("VRAM freed: %d MB used", torch.cuda.memory_allocated(0) // 1024 // 1024)


def _load_pipeline():
    global _pipeline, _model_name, _last_used
    _last_used = time.time()
    if _pipeline is not None:
        return _pipeline

    # Tell Ollama to unload models before we load SDXL
    try:
        import httpx
        httpx.post("http://localhost:11434/api/generate",
                   json={"model": "", "keep_alive": 0}, timeout=5)
        logger.info("Asked Ollama to unload models")
    except Exception:
        pass
    from diffusers import StableDiffusionXLPipeline, EulerDiscreteScheduler
    logger.info("Loading %s on %s (%d MB VRAM)", DEFAULT_MODEL,
                torch.cuda.get_device_name(0),
                torch.cuda.get_device_properties(0).total_mem // 1024 // 1024)
    pipe = StableDiffusionXLPipeline.from_pretrained(
        DEFAULT_MODEL, torch_dtype=torch.float16, variant="fp16", use_safetensors=True)
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config)
    pipe = pipe.to("cuda")
    pipe.enable_attention_slicing()
    try:
        pipe.enable_xformers_memory_efficient_attention()
    except Exception:
        pass
    _pipeline = pipe
    _model_name = DEFAULT_MODEL
    logger.info("SDXL ready")
    return pipe

@app.on_event("startup")
async def _start_idle_checker():
    import asyncio
    async def _check_idle():
        while True:
            await asyncio.sleep(60)
            if _pipeline is not None and (time.time() - _last_used) > IDLE_TIMEOUT:
                _unload_pipeline()
    asyncio.create_task(_check_idle())

@app.get("/health")
async def health():
    gpu_ok = torch.cuda.is_available()
    return {"status": "ready" if _pipeline else "idle", "model": _model_name,
            "gpu": torch.cuda.get_device_name(0) if gpu_ok else None,
            "vram_total_mb": torch.cuda.get_device_properties(0).total_mem // 1024 // 1024 if gpu_ok else 0,
            "gpu_available": gpu_ok}

@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    global _last_used
    _last_used = time.time()
    pipe = _load_pipeline()
    seed = req.seed if req.seed >= 0 else int(torch.randint(0, 2**32, (1,)).item())
    generator = torch.Generator(device="cuda").manual_seed(seed)
    filename = f"sdxl_{uuid.uuid4().hex[:8]}.png"
    output_path = OUTPUT_DIR / filename
    logger.info("Generating: %s... (%dx%d, %d steps)", req.prompt[:60], req.width, req.height, req.steps)
    start = time.time()
    try:
        result = pipe(prompt=req.prompt, negative_prompt=req.negative_prompt,
                      width=req.width, height=req.height, num_inference_steps=req.steps,
                      guidance_scale=req.guidance_scale, generator=generator)
        result.images[0].save(str(output_path))
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise HTTPException(status_code=503, detail="GPU OOM")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    elapsed_ms = int((time.time() - start) * 1000)
    logger.info("Generated: %s (%d ms)", filename, elapsed_ms)
    return GenerateResponse(image_path=str(output_path), filename=filename,
                            width=req.width, height=req.height,
                            model=_model_name, generation_time_ms=elapsed_ms, seed=seed)

@app.get("/images/{filename}")
async def get_image(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type="image/png")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9836)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    logger.info("SDXL server starting on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
