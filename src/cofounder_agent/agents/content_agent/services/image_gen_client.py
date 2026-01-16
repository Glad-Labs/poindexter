import logging
import io
from typing import Optional, List, Dict

import torch
from diffusers import StableDiffusionXLPipeline

import config


class ImageGenClient:
    def __init__(self):
        self.pipe = None
        self._initialize_model()

    def _initialize_model(self):
        try:
            if torch.cuda.is_available():
                logging.info("Loading Stable Diffusion XL model...")
                self.pipe = StableDiffusionXLPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    torch_dtype=torch.float16,
                    use_safetensors=True,
                    variant="fp16",
                ).to("cuda")
                logging.info("Stable Diffusion XL model loaded successfully.")
            else:
                logging.warning("CUDA not available. Image generation will be skipped.")
        except Exception as e:
            logging.error(f"Failed to load Stable Diffusion model: {e}")
            self.pipe = None

    def generate_images(self, prompt: str, output_path: str):
        """Generates an image using Stable Diffusion XL and saves it to the specified path."""
        try:
            logging.info(f"Generating image for prompt: '{prompt}'")
            # FIX: Add the negative_prompt from the config to improve image quality.
            image = self.pipe(
                prompt=prompt,
                negative_prompt=config.SD_NEGATIVE_PROMPT,
                num_inference_steps=30,
                guidance_scale=7.5,
            ).images[0]

            # Save the generated image to the provided output path
            image.save(output_path)

        except Exception as e:
            logging.error(f"Error generating image with Stable Diffusion: {e}", exc_info=True)
