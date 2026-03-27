import logging

logger = logging.getLogger(__name__)

import torch  # type: ignore
from diffusers import StableDiffusionXLPipeline  # type: ignore[attr-defined]

import config


class ImageGenClient:
    def __init__(self):
        self.pipe = None
        self._initialize_model()

    def _initialize_model(self):
        try:
            if torch.cuda.is_available():
                logger.info("Loading Stable Diffusion XL model...")
                self.pipe = StableDiffusionXLPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    torch_dtype=torch.float16,
                    use_safetensors=True,
                    variant="fp16",
                ).to("cuda")
                logger.info("Stable Diffusion XL model loaded successfully.")
            else:
                logger.warning("CUDA not available. Image generation will be skipped.")
        except Exception as e:
            logger.error(f"Failed to load Stable Diffusion model: {e}", exc_info=True)
            self.pipe = None

    def generate_images(self, prompt: str, output_path: str):
        """Generates an image using Stable Diffusion XL and saves it to the specified path."""
        try:
            logger.info(f"Generating image for prompt: '{prompt}'")
            # FIX: Add the negative_prompt from the config to improve image quality.
            image = self.pipe(  # type: ignore[misc]
                prompt=prompt,
                negative_prompt=config.SD_NEGATIVE_PROMPT,  # type: ignore[attr-defined]
                num_inference_steps=30,
                guidance_scale=7.5,
            ).images[
                0
            ]  # type: ignore[index]

            # Save the generated image to the provided output path
            image.save(output_path)

        except Exception as e:
            logger.error(f"Error generating image with Stable Diffusion: {e}", exc_info=True)
