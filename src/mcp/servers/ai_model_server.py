"""
AI Model MCP Server for Glad Labs

Provides centralized AI model access with flexible provider and model selection.
Supports OpenAI, Google Gemini, and local Ollama models with cost optimization.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from enum import Enum

# AI Provider clients
import openai
try:
    import google.genai as genai
except ImportError:
    # Fallback to old deprecated package if new one not available
    import google.generativeai as genai
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


class ModelProvider(str, Enum):
    """Available AI model providers"""
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"


class CostTier(str, Enum):
    """Cost optimization tiers for model selection"""
    ULTRA_CHEAP = "ultra_cheap"  # Cheapest possible
    CHEAP = "cheap"              # Cost-optimized
    BALANCED = "balanced"        # Performance/cost balance
    PREMIUM = "premium"          # High performance
    ULTRA_PREMIUM = "ultra_premium"  # Best available


@dataclass
class ModelConfig:
    """Configuration for an AI model"""
    provider: ModelProvider
    model_name: str
    cost_tier: CostTier
    max_tokens: int = 4000
    temperature: float = 0.7
    supports_tools: bool = False
    cost_per_1k_tokens: float = 0.0  # For cost tracking


class AIModelServer:
    """
    MCP Server providing AI model access with flexible provider selection.
    
    This server allows agents to:
    1. Choose specific models for different tasks
    2. Optimize costs by using appropriate model tiers
    3. Use local models during development
    4. Track usage and costs
    """
    
    def __init__(self, name: str = "ai-model-server"):
        self.name = name
        self.logger = logging.getLogger(f"mcp.{name}")
        
        # Initialize providers
        self._setup_providers()
        
        # Model configurations by cost tier
        self.models = self._configure_models()
        
        # Usage tracking
        self.usage_stats = {
            "total_requests": 0,
            "total_tokens": 0,
            "estimated_cost": 0.0,
            "by_provider": {},
            "by_model": {}
        }
    
    def _setup_providers(self):
        """Initialize AI provider clients"""
        
        # OpenAI
        if os.getenv("OPENAI_API_KEY"):
            openai.api_key = os.getenv("OPENAI_API_KEY")
            self.openai_client = openai.OpenAI()
            self.logger.info("OpenAI client initialized")
        else:
            self.openai_client = None
            self.logger.warning("OpenAI API key not found")
        
        # Google Gemini
        if os.getenv("GEMINI_API_KEY"):
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            self.logger.info("Gemini client initialized")
        else:
            self.logger.warning("Gemini API key not found")
        
        # Ollama (local)
        if OLLAMA_AVAILABLE:
            try:
                # Test connection
                ollama.list()
                self.ollama_available = True
                self.logger.info("Ollama client available")
            except Exception as e:
                self.ollama_available = False
                self.logger.warning(f"Ollama not available: {e}")
        else:
            self.ollama_available = False
            self.logger.warning("Ollama package not installed")
    
    def _configure_models(self) -> Dict[CostTier, List[ModelConfig]]:
        """Configure available models by cost tier"""
        models = {
            CostTier.ULTRA_CHEAP: [],
            CostTier.CHEAP: [],
            CostTier.BALANCED: [],
            CostTier.PREMIUM: [],
            CostTier.ULTRA_PREMIUM: []
        }
        
        # Local models (free during development)
        if self.ollama_available:
            models[CostTier.ULTRA_CHEAP].extend([
                ModelConfig(ModelProvider.OLLAMA, "llama3.2:1b", CostTier.ULTRA_CHEAP, 4000, 0.7, False, 0.0),
                ModelConfig(ModelProvider.OLLAMA, "phi3:mini", CostTier.ULTRA_CHEAP, 4000, 0.7, False, 0.0),
            ])
            models[CostTier.CHEAP].extend([
                ModelConfig(ModelProvider.OLLAMA, "llama3.2:3b", CostTier.CHEAP, 4000, 0.7, False, 0.0),
                ModelConfig(ModelProvider.OLLAMA, "mistral:7b", CostTier.CHEAP, 8000, 0.7, False, 0.0),
            ])
            models[CostTier.BALANCED].extend([
                ModelConfig(ModelProvider.OLLAMA, "llama3.1:8b", CostTier.BALANCED, 8000, 0.7, False, 0.0),
                ModelConfig(ModelProvider.OLLAMA, "qwen2.5:7b", CostTier.BALANCED, 8000, 0.7, False, 0.0),
            ])
        
        # Gemini models
        if os.getenv("GEMINI_API_KEY"):
            models[CostTier.CHEAP].append(
                ModelConfig(ModelProvider.GEMINI, "gemini-1.5-flash-8b", CostTier.CHEAP, 8000, 0.7, True, 0.075)
            )
            models[CostTier.BALANCED].append(
                ModelConfig(ModelProvider.GEMINI, "gemini-1.5-flash", CostTier.BALANCED, 8000, 0.7, True, 0.075)
            )
            models[CostTier.PREMIUM].append(
                ModelConfig(ModelProvider.GEMINI, "gemini-1.5-pro", CostTier.PREMIUM, 32000, 0.7, True, 1.25)
            )
            models[CostTier.ULTRA_PREMIUM].append(
                ModelConfig(ModelProvider.GEMINI, "gemini-1.0-pro", CostTier.ULTRA_PREMIUM, 32000, 0.7, True, 1.25)
            )
        
        # OpenAI models
        if self.openai_client:
            models[CostTier.CHEAP].append(
                ModelConfig(ModelProvider.OPENAI, "gpt-4o-mini", CostTier.CHEAP, 4000, 0.7, True, 0.15)
            )
            models[CostTier.BALANCED].append(
                ModelConfig(ModelProvider.OPENAI, "gpt-4o", CostTier.BALANCED, 4000, 0.7, True, 2.5)
            )
            models[CostTier.PREMIUM].append(
                ModelConfig(ModelProvider.OPENAI, "gpt-4o", CostTier.PREMIUM, 8000, 0.7, True, 2.5)
            )
            models[CostTier.ULTRA_PREMIUM].append(
                ModelConfig(ModelProvider.OPENAI, "o1-preview", CostTier.ULTRA_PREMIUM, 32000, 1.0, False, 15.0)
            )
        
        return models
    
    async def generate_text(self, prompt: str, cost_tier: str = "balanced", 
                          specific_model: Optional[str] = None,
                          max_tokens: Optional[int] = None,
                          temperature: Optional[float] = None) -> Dict[str, Any]:
        """
        Generate text using specified cost tier or specific model.
        
        Args:
            prompt: Input prompt
            cost_tier: Cost optimization tier
            specific_model: Specific model to use (overrides cost_tier)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            
        Returns:
            Dict with generated text, model used, and usage info
        """
        try:
            # Select model
            if specific_model:
                model_config = self._find_model_by_name(specific_model)
                if not model_config:
                    raise ValueError(f"Model '{specific_model}' not found")
            else:
                tier = CostTier(cost_tier)
                model_config = self._select_best_model(tier)
                if not model_config:
                    raise ValueError(f"No models available for tier '{cost_tier}'")
            
            # Override config parameters if provided
            if max_tokens:
                model_config.max_tokens = max_tokens
            if temperature is not None:
                model_config.temperature = temperature
            
            self.logger.info(f"Using model: {model_config.model_name} (Provider: {model_config.provider})")
            
            # Generate text based on provider
            if model_config.provider == ModelProvider.OLLAMA:
                result = await self._generate_ollama(prompt, model_config)
            elif model_config.provider == ModelProvider.GEMINI:
                result = await self._generate_gemini(prompt, model_config)
            elif model_config.provider == ModelProvider.OPENAI:
                result = await self._generate_openai(prompt, model_config)
            else:
                raise ValueError(f"Unsupported provider: {model_config.provider}")
            
            # Track usage
            self._track_usage(model_config, result.get("tokens_used", 0))
            
            return {
                "text": result["text"],
                "model_used": model_config.model_name,
                "provider": model_config.provider.value,
                "cost_tier": model_config.cost_tier.value,
                "tokens_used": result.get("tokens_used", 0),
                "estimated_cost": result.get("estimated_cost", 0.0)
            }
            
        except Exception as e:
            self.logger.error(f"Error generating text: {e}")
            return {
                "error": str(e),
                "text": "",
                "model_used": "",
                "provider": "",
                "cost_tier": cost_tier,
                "tokens_used": 0,
                "estimated_cost": 0.0
            }
    
    def _find_model_by_name(self, model_name: str) -> Optional[ModelConfig]:
        """Find model configuration by name"""
        for tier_models in self.models.values():
            for model in tier_models:
                if model.model_name == model_name:
                    return model
        return None
    
    def _select_best_model(self, tier: CostTier) -> Optional[ModelConfig]:
        """Select the best available model for a cost tier"""
        # Try exact tier first
        if self.models[tier]:
            return self.models[tier][0]
        
        # Fallback to cheaper tiers
        tier_order = [CostTier.ULTRA_CHEAP, CostTier.CHEAP, CostTier.BALANCED, 
                     CostTier.PREMIUM, CostTier.ULTRA_PREMIUM]
        
        current_index = tier_order.index(tier)
        
        # Try cheaper tiers first
        for i in range(current_index, -1, -1):
            if self.models[tier_order[i]]:
                return self.models[tier_order[i]][0]
        
        # Try more expensive tiers
        for i in range(current_index + 1, len(tier_order)):
            if self.models[tier_order[i]]:
                return self.models[tier_order[i]][0]
        
        return None
    
    async def _generate_ollama(self, prompt: str, config: ModelConfig) -> Dict[str, Any]:
        """Generate text using Ollama"""
        if not self.ollama_available:
            raise RuntimeError("Ollama is not available")
        
        response = ollama.generate(
            model=config.model_name,
            prompt=prompt,
            options={
                "temperature": config.temperature,
                "num_predict": config.max_tokens
            }
        )
        
        return {
            "text": response["response"],
            "tokens_used": len(response["response"].split()) * 1.3,  # Rough estimate
            "estimated_cost": 0.0  # Local models are free
        }
    
    async def _generate_gemini(self, prompt: str, config: ModelConfig) -> Dict[str, Any]:
        """Generate text using Google Gemini"""
        model = genai.GenerativeModel(config.model_name)
        
        generation_config = genai.types.GenerationConfig(
            temperature=config.temperature,
            max_output_tokens=config.max_tokens
        )
        
        response = model.generate_content(prompt, generation_config=generation_config)
        
        # Estimate tokens (Gemini doesn't always return usage)
        estimated_tokens = len(prompt.split()) + len(response.text.split()) if response.text else 0
        estimated_cost = (estimated_tokens / 1000) * config.cost_per_1k_tokens
        
        return {
            "text": response.text if response.text else "",
            "tokens_used": estimated_tokens,
            "estimated_cost": estimated_cost
        }
    
    async def _generate_openai(self, prompt: str, config: ModelConfig) -> Dict[str, Any]:
        """Generate text using OpenAI"""
        if not self.openai_client:
            raise RuntimeError("OpenAI client not available")
        
        response = self.openai_client.chat.completions.create(
            model=config.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=config.max_tokens,
            temperature=config.temperature
        )
        
        tokens_used = response.usage.total_tokens if response.usage else 0
        estimated_cost = (tokens_used / 1000) * config.cost_per_1k_tokens
        
        return {
            "text": response.choices[0].message.content if response.choices else "",
            "tokens_used": tokens_used,
            "estimated_cost": estimated_cost
        }
    
    def _track_usage(self, config: ModelConfig, tokens_used: int):
        """Track usage statistics"""
        self.usage_stats["total_requests"] += 1
        self.usage_stats["total_tokens"] += tokens_used
        self.usage_stats["estimated_cost"] += (tokens_used / 1000) * config.cost_per_1k_tokens
        
        # By provider
        provider_key = config.provider.value
        if provider_key not in self.usage_stats["by_provider"]:
            self.usage_stats["by_provider"][provider_key] = {"requests": 0, "tokens": 0, "cost": 0.0}
        
        self.usage_stats["by_provider"][provider_key]["requests"] += 1
        self.usage_stats["by_provider"][provider_key]["tokens"] += tokens_used
        self.usage_stats["by_provider"][provider_key]["cost"] += (tokens_used / 1000) * config.cost_per_1k_tokens
        
        # By model
        model_key = config.model_name
        if model_key not in self.usage_stats["by_model"]:
            self.usage_stats["by_model"][model_key] = {"requests": 0, "tokens": 0, "cost": 0.0}
        
        self.usage_stats["by_model"][model_key]["requests"] += 1
        self.usage_stats["by_model"][model_key]["tokens"] += tokens_used
        self.usage_stats["by_model"][model_key]["cost"] += (tokens_used / 1000) * config.cost_per_1k_tokens
    
    async def get_available_models(self) -> Dict[str, Any]:
        """Get list of all available models organized by cost tier"""
        available = {}
        
        for tier, models in self.models.items():
            if models:  # Only include tiers with available models
                available[tier.value] = [
                    {
                        "name": model.model_name,
                        "provider": model.provider.value,
                        "max_tokens": model.max_tokens,
                        "supports_tools": model.supports_tools,
                        "cost_per_1k_tokens": model.cost_per_1k_tokens
                    }
                    for model in models
                ]
        
        return {
            "available_models": available,
            "total_models": sum(len(models) for models in self.models.values()),
            "providers_available": {
                "openai": self.openai_client is not None,
                "gemini": os.getenv("GEMINI_API_KEY") is not None,
                "ollama": self.ollama_available
            }
        }
    
    async def get_usage_stats(self) -> Dict[str, Any]:
        """Get current usage statistics"""
        return self.usage_stats.copy()


# Simple MCP-like interface for now (will be replaced with actual MCP when package is available)
async def main():
    """
    Temporary main function for testing the AI Model Server
    """
    logging.basicConfig(level=logging.INFO)
    
    server = AIModelServer()
    
    # Test different cost tiers
    test_prompt = "Explain the benefits of using AI in content creation in one paragraph."
    
    print("=== AI Model Server Test ===")
    
    # Test ultra cheap tier (should use local if available)
    print("\n1. Ultra Cheap Tier:")
    result = await server.generate_text(test_prompt, "ultra_cheap")
    print(f"Model: {result['model_used']} (Provider: {result['provider']})")
    print(f"Text: {result['text'][:100]}...")
    print(f"Cost: ${result['estimated_cost']:.4f}")
    
    # Test balanced tier
    print("\n2. Balanced Tier:")
    result = await server.generate_text(test_prompt, "balanced")
    print(f"Model: {result['model_used']} (Provider: {result['provider']})")
    print(f"Text: {result['text'][:100]}...")
    print(f"Cost: ${result['estimated_cost']:.4f}")
    
    # Show available models
    print("\n3. Available Models:")
    models = await server.get_available_models()
    for tier, model_list in models["available_models"].items():
        print(f"  {tier}: {len(model_list)} models")
    
    # Show usage stats
    print("\n4. Usage Statistics:")
    stats = await server.get_usage_stats()
    print(f"Total requests: {stats['total_requests']}")
    print(f"Total cost: ${stats['estimated_cost']:.4f}")


if __name__ == "__main__":
    asyncio.run(main())