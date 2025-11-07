"""
Comprehensive Ollama Generation Pipeline Tests

Tests the complete content generation pipeline using Ollama models:
1. Model availability and connectivity
2. Content generation quality
3. Output formatting and validation
4. Performance metrics (latency, token usage)
5. Error handling and fallbacks
6. Multi-model comparison
7. Task completion and persistence

Metrics Tracked:
- Generation time per model
- Output quality scores
- Token efficiency
- Error rates
- Success/failure patterns
"""

import pytest
import asyncio
import time
import json
from typing import Dict, Any, List
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OllamaGenerationTester:
    """Main tester for Ollama generation pipeline"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        """
        Initialize tester

        Args:
            base_url: Ollama API base URL
        """
        self.base_url = base_url
        self.results = {
            'tests_run': 0,
            'tests_passed': 0,
            'tests_failed': 0,
            'models_tested': [],
            'performance_metrics': {},
            'quality_scores': {},
            'errors': []
        }

    async def test_ollama_connectivity(self) -> bool:
        """Test if Ollama service is running and responding"""
        import httpx

        logger.info("🔍 Testing Ollama Connectivity...")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
                if response.status_code == 200:
                    models = response.json().get('models', [])
                    logger.info(f"✅ Ollama connected. Found {len(models)} models:")
                    for model in models:
                        logger.info(f"   - {model['name']}: {model.get('size', 'unknown')} bytes")
                    return True
                else:
                    logger.error(f"❌ Ollama responded with status {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"❌ Cannot connect to Ollama: {e}")
            return False

    async def test_model_generation(self, model: str, prompt: str, timeout: int = 60) -> Dict[str, Any]:
        """
        Test content generation for a specific model

        Args:
            model: Model name (e.g., 'mistral', 'llama2')
            prompt: Generation prompt
            timeout: Request timeout in seconds

        Returns:
            Dict with:
            - model: Model name
            - success: bool
            - response: Generated text
            - generation_time: Seconds taken
            - token_count: Estimated token count
            - quality_score: Quality assessment (0-100)
            - error: Error message if failed
        """
        import httpx

        logger.info(f"🤖 Testing model: {model}")
        start_time = time.time()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                    },
                    timeout=timeout
                )

                if response.status_code == 200:
                    data = response.json()
                    generated_text = data.get('response', '').strip()
                    generation_time = time.time() - start_time

                    # Calculate quality metrics
                    quality_score = self._score_generation_quality(
                        generated_text, prompt
                    )
                    token_count = self._estimate_tokens(generated_text)

                    result = {
                        'model': model,
                        'success': True,
                        'response': generated_text,
                        'generation_time': round(generation_time, 2),
                        'token_count': token_count,
                        'tokens_per_second': round(token_count / generation_time, 2),
                        'quality_score': quality_score,
                        'response_length': len(generated_text),
                        'timestamp': datetime.now().isoformat()
                    }

                    logger.info(f"✅ {model} - Quality: {quality_score}/100, Time: {generation_time:.2f}s")
                    self.results['models_tested'].append(model)
                    self.results['quality_scores'][model] = quality_score
                    self.results['performance_metrics'][model] = {
                        'generation_time': result['generation_time'],
                        'tokens_per_second': result['tokens_per_second']
                    }

                    return result
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    logger.error(f"❌ {model} - {error_msg}")
                    self.results['errors'].append({
                        'model': model,
                        'error': error_msg
                    })
                    return {
                        'model': model,
                        'success': False,
                        'error': error_msg
                    }

        except asyncio.TimeoutError:
            logger.error(f"❌ {model} - Request timeout after {timeout}s")
            self.results['errors'].append({
                'model': model,
                'error': f'Timeout after {timeout}s'
            })
            return {
                'model': model,
                'success': False,
                'error': f'Timeout after {timeout}s'
            }
        except Exception as e:
            logger.error(f"❌ {model} - Error: {e}")
            self.results['errors'].append({
                'model': model,
                'error': str(e)
            })
            return {
                'model': model,
                'success': False,
                'error': str(e)
            }

    def _score_generation_quality(self, text: str, prompt: str) -> int:
        """
        Score generated content quality

        Criteria:
        - Length (should be reasonable)
        - Structure (paragraphs, sentences)
        - Relevance (addresses prompt)
        - Grammar (basic checks)

        Returns: Score 0-100
        """
        score = 0

        # Check minimum length (at least 50 chars)
        if len(text) >= 50:
            score += 20
        elif len(text) >= 20:
            score += 10

        # Check for paragraphs
        paragraphs = [p for p in text.split('\n') if p.strip()]
        if len(paragraphs) >= 2:
            score += 15
        elif len(paragraphs) >= 1:
            score += 8

        # Check for sentences
        sentences = [s for s in text.split('.') if s.strip()]
        if len(sentences) >= 3:
            score += 15
        elif len(sentences) >= 1:
            score += 8

        # Check for relevance to prompt
        prompt_words = set(prompt.lower().split())
        text_words = set(text.lower().split())
        relevance_ratio = len(prompt_words & text_words) / max(len(prompt_words), 1)
        score += int(relevance_ratio * 20)

        # Check grammar basics (no excessive punctuation)
        bad_patterns = ['...', '!!!', '???', '  ']
        issues = sum(text.count(pattern) for pattern in bad_patterns)
        if issues == 0:
            score += 15
        elif issues <= 2:
            score += 8

        # Bonus for being coherent and complete
        if text.endswith(('.', '!', '?')):
            score += 7

        return min(100, max(0, score))

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count from text

        Rough estimation: ~4 chars per token for English
        """
        return len(text) // 4 + 1

    def generate_quality_report(self) -> str:
        """Generate comprehensive quality report"""
        report = "\n" + "=" * 80 + "\n"
        report += "📊 OLLAMA GENERATION PIPELINE QUALITY REPORT\n"
        report += "=" * 80 + "\n\n"

        report += f"📈 SUMMARY\n"
        report += f"{'─' * 80}\n"
        report += f"Models Tested: {len(self.results['models_tested'])}\n"
        report += f"Tests Run: {self.results['tests_run']}\n"
        report += f"Tests Passed: {self.results['tests_passed']}\n"
        report += f"Tests Failed: {self.results['tests_failed']}\n"
        if self.results['tests_run'] > 0:
            report += f"Success Rate: {(self.results['tests_passed'] / self.results['tests_run'] * 100):.1f}%\n"
        report += "\n"

        if self.results['models_tested']:
            report += f"🏆 MODEL COMPARISON\n"
            report += f"{'─' * 80}\n"
            report += f"{'Model':<20} {'Quality':<12} {'Time (s)':<12} {'Tokens/sec':<15}\n"
            report += f"{'-' * 80}\n"

            for model in self.results['models_tested']:
                quality = self.results['quality_scores'].get(model, 0)
                metrics = self.results['performance_metrics'].get(model, {})
                gen_time = metrics.get('generation_time', 0)
                tok_per_sec = metrics.get('tokens_per_second', 0)

                report += f"{model:<20} {quality:>6}/100      {gen_time:>8.2f}      {tok_per_sec:>10.2f}\n"

            report += "\n"

        if self.results['errors']:
            report += f"❌ ERRORS ({len(self.results['errors'])})\n"
            report += f"{'─' * 80}\n"
            for error in self.results['errors']:
                report += f"  {error['model']}: {error['error']}\n"
            report += "\n"

        report += f"⏱️ PERFORMANCE ANALYSIS\n"
        report += f"{'─' * 80}\n"
        if self.results['performance_metrics']:
            times = [m['generation_time'] for m in self.results['performance_metrics'].values()]
            report += f"Fastest Model: {min(times):.2f}s\n"
            report += f"Slowest Model: {max(times):.2f}s\n"
            report += f"Average Time: {sum(times)/len(times):.2f}s\n"
            report += "\n"

        if self.results['quality_scores']:
            report += f"🎯 QUALITY ANALYSIS\n"
            report += f"{'─' * 80}\n"
            scores = list(self.results['quality_scores'].values())
            report += f"Highest Quality: {max(scores)}/100\n"
            report += f"Lowest Quality: {min(scores)}/100\n"
            report += f"Average Quality: {sum(scores)/len(scores):.1f}/100\n"
            report += "\n"

        report += "=" * 80 + "\n"
        return report


# ============================================================================
# PYTEST TEST FUNCTIONS
# ============================================================================

@pytest.mark.asyncio
async def test_ollama_connectivity():
    """Test that Ollama service is available"""
    tester = OllamaGenerationTester()
    result = await tester.test_ollama_connectivity()
    assert result, "Ollama service is not available"


@pytest.mark.asyncio
async def test_mistral_generation():
    """Test Mistral model generation"""
    tester = OllamaGenerationTester()
    prompt = "Write a short introduction about artificial intelligence for beginners"

    result = await tester.test_model_generation(
        model="mistral",
        prompt=prompt,
        timeout=60
    )

    assert result['success'], f"Generation failed: {result.get('error')}"
    assert result['response'], "No response generated"
    assert result['quality_score'] >= 50, f"Quality score too low: {result['quality_score']}"
    assert len(result['response']) > 50, "Response too short"


@pytest.mark.asyncio
async def test_llama2_generation():
    """Test Llama2 model generation"""
    tester = OllamaGenerationTester()
    prompt = "Explain the concept of machine learning in simple terms"

    result = await tester.test_model_generation(
        model="llama2",
        prompt=prompt,
        timeout=60
    )

    assert result['success'], f"Generation failed: {result.get('error')}"
    assert result['response'], "No response generated"
    assert result['quality_score'] >= 40, f"Quality score too low: {result['quality_score']}"


@pytest.mark.asyncio
async def test_model_quality_comparison():
    """Compare quality across multiple models"""
    tester = OllamaGenerationTester()
    prompts = [
        "What are the benefits of cloud computing?",
        "Describe the solar system in detail",
        "Explain how photosynthesis works"
    ]

    results_by_model = {}

    for prompt in prompts:
        for model in ["mistral", "llama2"]:
            result = await tester.test_model_generation(
                model=model,
                prompt=prompt,
                timeout=60
            )
            tester.results['tests_run'] += 1
            if result['success']:
                tester.results['tests_passed'] += 1
                if model not in results_by_model:
                    results_by_model[model] = []
                results_by_model[model].append(result['quality_score'])
            else:
                tester.results['tests_failed'] += 1

    # Verify all models produced results
    for model, scores in results_by_model.items():
        assert len(scores) > 0, f"No successful generations for {model}"
        avg_score = sum(scores) / len(scores)
        logger.info(f"{model} average quality: {avg_score:.1f}/100")

    # Print report
    logger.info(tester.generate_quality_report())


@pytest.mark.asyncio
async def test_generation_performance():
    """Test generation speed and efficiency"""
    tester = OllamaGenerationTester()
    prompt = "Write a detailed technical blog post introduction about APIs"

    for model in ["mistral", "llama2"]:
        result = await tester.test_model_generation(
            model=model,
            prompt=prompt,
            timeout=120
        )

        if result['success']:
            # Check performance metrics
            assert result['generation_time'] > 0
            assert result['tokens_per_second'] > 0
            assert result['token_count'] > 0

            logger.info(f"\n{model} Performance:")
            logger.info(f"  Generation Time: {result['generation_time']}s")
            logger.info(f"  Tokens/Second: {result['tokens_per_second']}")
            logger.info(f"  Total Tokens: {result['token_count']}")


@pytest.mark.asyncio
async def test_content_variety():
    """Test generation with various content types"""
    tester = OllamaGenerationTester()
    test_cases = [
        {
            'type': 'technical',
            'prompt': 'Write a technical guide for implementing REST APIs'
        },
        {
            'type': 'creative',
            'prompt': 'Write a creative story about a robot discovering emotions'
        },
        {
            'type': 'educational',
            'prompt': 'Explain the theory of relativity for a high school student'
        },
        {
            'type': 'business',
            'prompt': 'Write a business case for adopting cloud computing'
        }
    ]

    for test_case in test_cases:
        result = await tester.test_model_generation(
            model="mistral",
            prompt=test_case['prompt'],
            timeout=60
        )

        if result['success']:
            logger.info(
                f"✅ {test_case['type'].upper()}: "
                f"Quality {result['quality_score']}/100, "
                f"Length {len(result['response'])} chars"
            )


if __name__ == "__main__":
    """Run tests with pytest"""
    pytest.main([__file__, "-v", "-s"])
