"""
Unified AI Content Generator Service

Handles blog post generation with fallback through multiple providers:
1. Local Ollama (free, RTX 5070 optimized)
2. HuggingFace (free tier)
3. Google Gemini (paid fallback)

Features:
- Intelligent provider fallback strategy
- Self-checking and validation throughout generation
- Quality assurance with refinement loops
- Content metrics and performance tracking

ASYNC-FIRST: All I/O operations use httpx async client (no blocking calls)
"""

import os
import logging
from typing import Optional, Tuple, Dict, Any
import asyncio
import re
import time
import httpx

logger = logging.getLogger(__name__)


class ContentValidationResult:
    """Result of content validation check"""
    
    def __init__(self, is_valid: bool, quality_score: float, issues: Optional[list[str]] = None, feedback: str = ""):
        self.is_valid = is_valid
        self.quality_score = quality_score  # 0-10 scale
        self.issues = issues if issues is not None else []
        self.feedback = feedback


class AIContentGenerator:
    """Unified content generation with provider fallback and self-checking"""

    def __init__(self, quality_threshold: float = 7.0):
        """Initialize content generator
        
        Args:
            quality_threshold: Minimum quality score (0-10) for content acceptance
        """
        self.quality_threshold = quality_threshold
        self.ollama_available = False
        self.ollama_checked = False  # Track if we've checked Ollama async
        self.hf_token = os.getenv("HUGGINGFACE_API_TOKEN")
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.generation_attempts = 0
        self.max_refinement_attempts = 3
        
        logger.info("AIContentGenerator initialized (Ollama check deferred to first async call)")

    async def _check_ollama_async(self):
        """Async check if Ollama is running - call this once before using Ollama"""
        if self.ollama_checked:
            return
        
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get("http://localhost:11434/api/tags")
                self.ollama_available = response.status_code == 200
            
            if self.ollama_available:
                logger.info("✓ Ollama available at http://localhost:11434")
            else:
                logger.debug("Ollama not available (status check failed)")
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            self.ollama_available = False
        finally:
            self.ollama_checked = True

    def _validate_content(self, content: str, topic: str, target_length: int) -> ContentValidationResult:
        """
        Self-check: Validate generated content against quality rubric.
        
        Checks:
        1. Content length (target ±30%)
        2. Structure (has headings, sections)
        3. Content quality (readability, completeness)
        4. Markdown formatting
        5. Presence of practical examples
        
        Returns:
            ContentValidationResult with quality score and issues
        """
        issues = []
        score = 10.0
        
        # 1. Check length
        word_count = len(content.split())
        min_words = int(target_length * 0.7)
        max_words = int(target_length * 1.3)
        
        if word_count < min_words:
            issues.append(f"Content too short: {word_count} words (target: {target_length})")
            score -= 2.0
        elif word_count > max_words:
            issues.append(f"Content too long: {word_count} words (target: {target_length})")
            score -= 1.0
        
        # 2. Check structure (headings)
        heading_count = len(re.findall(r'^##+ ', content, re.MULTILINE))
        if heading_count < 3:
            issues.append(f"Insufficient structure: {heading_count} sections (recommend 3-5)")
            score -= 1.5
        
        # 3. Check for introduction
        if not re.search(r'^# ', content, re.MULTILINE):
            issues.append("Missing title (# heading)")
            score -= 1.0
        
        # 4. Check for conclusion
        conclusion_keywords = ['conclusion', 'summary', 'next steps', 'takeaway']
        has_conclusion = any(keyword in content.lower() for keyword in conclusion_keywords)
        if not has_conclusion:
            issues.append("Missing conclusion section")
            score -= 1.5
        
        # 5. Check for practical examples/lists
        has_examples = '- ' in content or '* ' in content or '1. ' in content
        if not has_examples:
            issues.append("Missing practical examples or bullet points")
            score -= 1.0
        
        # 6. Check for call-to-action
        cta_keywords = ['ready', 'start', 'begin', 'try', 'implement', 'action', 'next']
        has_cta = any(keyword in content.lower() for keyword in cta_keywords)
        if not has_cta:
            issues.append("Missing call-to-action")
            score -= 0.5
        
        # 7. Check for topic mentions (relevance)
        topic_words = topic.lower().split()[:3]  # First 3 words
        topic_mentions = sum(1 for word in topic_words if word in content.lower())
        if topic_mentions < 2:
            issues.append(f"Topic '{topic}' mentioned too few times")
            score -= 1.0
        
        # Ensure score stays in valid range
        score = max(0, min(10, score))
        
        is_valid = score >= self.quality_threshold
        
        feedback = ""
        if is_valid:
            feedback = f"✓ Content approved (quality score: {score:.1f}/10)"
        else:
            feedback = f"✗ Content needs improvement (quality score: {score:.1f}/10, threshold: {self.quality_threshold})"
        
        logger.info(f"Content validation: {feedback}")
        if issues:
            logger.debug(f"Issues found: {issues}")
        
        return ContentValidationResult(
            is_valid=is_valid,
            quality_score=score,
            issues=issues,
            feedback=feedback
        )

    async def generate_blog_post(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: list[str],
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Generate a blog post using best available model with self-checking.
        
        Features:
        - Intelligent provider fallback (Ollama → HuggingFace → Gemini)
        - Self-validation and quality checking
        - Refinement loop for rejected content
        - Full metrics tracking
        
        Args:
            topic: Blog post topic
            style: Writing style (technical, narrative, etc.)
            tone: Content tone (professional, casual, etc.)
            target_length: Target word count
            tags: Content tags
            
        Returns:
            Tuple of (content, model_used, metrics_dict)
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"🎬 BLOG GENERATION STARTED")
        logger.info(f"{'='*80}")
        logger.info(f"📌 Topic: {topic}")
        logger.info(f"📌 Style: {style} | Tone: {tone}")
        logger.info(f"📌 Target length: {target_length} words | Tags: {tags}")
        logger.info(f"📌 Quality threshold: {self.quality_threshold}")
        logger.info(f"{'='*80}\n")
        
        # Build prompts
        system_prompt = f"""You are an expert technical writer and blogger.
Your writing style is {style}.
Your tone is {tone}.
Write for an educated but general audience.
Generate approximately {target_length} words.
Format as Markdown with proper headings (# for title, ## for sections, ### for subsections).
Include:
- Compelling introduction
- 3-5 main sections with practical insights
- Real-world examples or bullet points
- Clear conclusion with call-to-action
Tags: {', '.join(tags) if tags else 'general'}"""

        generation_prompt = f"""Write a professional blog post about: {topic}

Requirements:
- Target length: approximately {target_length} words
- Style: {style}
- Tone: {tone}
- Format: Markdown with clear structure
- Include practical examples and insights
- End with a clear call-to-action

Start writing now:"""

        # Refinement prompt for content that doesn't pass QA
        refinement_prompt_template = """The following blog post was rejected for quality reasons. Please improve it:

FEEDBACK: {feedback}

ISSUES: {issues}

Please rewrite the content addressing all issues. Keep the same structure but improve quality:

Original content:
{content}

Improved version:"""

        # Track metrics
        metrics = {
            "topic": topic,
            "generation_attempts": 0,
            "refinement_attempts": 0,
            "validation_results": [],
            "model_used": None,
            "final_quality_score": 0.0,
            "generation_time_seconds": 0
        }
        
        start_time = time.time()
        
        # Try models in order of preference
        attempts = []

        # 1. Try Ollama (local, free, no internet, RTX 5070 optimized)
        if self.ollama_available:
            logger.info(f"🔄 [ATTEMPT 1/3] Trying Ollama (Local, GPU-accelerated)...")
            logger.info(f"   ├─ Endpoint: http://localhost:11434")
            logger.info(f"   ├─ Model preference order: [neural-chat, mistral, llama2]")
            logger.info(f"   └─ Status: Checking connection...\n")
            try:
                from .ollama_client import OllamaClient
                ollama = OllamaClient()
                logger.info(f"   ✓ OllamaClient initialized")
                
                # Try stable, fast models first, avoid slow/problematic ones
                # neural-chat:latest - PROVEN RELIABLE & FAST ✓✓✓
                # mistral:latest - Fast but crashes with "llama runner process terminated"
                # llama2:latest - Reasonable but occasional timeouts
                # qwen2.5:14b - TOO SLOW (10-20 tokens/sec), causes timeouts
                # qwen3:14b - Better than qwen2.5 but still slow
                # deepseek-r1:14b - REMOVED (causes 500 errors, requires 16GB+ VRAM)
                # Priority: neural-chat (best) → llama2 → qwen2.5 (with timeout)
                model_list = ["neural-chat:latest", "llama2:latest", "qwen2:7b"]
                for model_idx, model_name in enumerate(model_list, 1):
                    try:
                        logger.info(f"   └─ Testing model {model_idx}/{len(model_list)}: {model_name}")
                        metrics["generation_attempts"] += 1
                        
                        logger.info(f"      ⏱️  Generating content (timeout: 120s)...")
                        response = await ollama.generate(
                            prompt=generation_prompt,
                            system=system_prompt,
                            model=model_name,
                            stream=False,
                        )
                        
                        # Extract text from response dict
                        # OllamaClient.generate() returns dict with 'text' key (not 'response')
                        logger.info(f"      📦 Raw response type: {type(response)}")
                        if isinstance(response, dict):
                            logger.info(f"      📦 Response is dict with keys: {list(response.keys())}")
                        
                        generated_content = ""
                        if isinstance(response, dict):
                            # Try multiple possible keys: 'text' (OllamaClient), 'response' (Ollama API), 'content'
                            generated_content = response.get("text", "") or response.get("response", "") or response.get("content", "")
                            logger.info(f"      📦 Extracted from dict: {len(generated_content)} chars")
                            if generated_content:
                                logger.debug(f"      📦 Response type: dict | Extracted text: {len(generated_content)} chars")
                            else:
                                logger.warning(f"      ⚠️  No text found in response dict keys: {list(response.keys())}")
                        elif isinstance(response, str):
                            generated_content = response
                            logger.info(f"      📦 Got direct string: {len(generated_content)} chars")
                            logger.debug(f"      📦 Response type: str | Content: {len(generated_content)} chars")
                        else:
                            logger.warning(f"      ⚠️  Unexpected response type: {type(response)}")
                            generated_content = ""
                        
                        logger.info(f"      🔍 Final check: bool(content)={bool(generated_content)}, len={len(generated_content)}, threshold=100")
                        if generated_content and len(generated_content) > 100:
                            logger.info(f"      ✓ Content generated: {len(generated_content)} characters")
                            
                            # Self-check: Validate content quality
                            logger.info(f"      🔍 Validating content quality...")
                            validation = self._validate_content(generated_content, topic, target_length)
                            metrics["validation_results"].append({
                                "attempt": metrics["generation_attempts"],
                                "score": validation.quality_score,
                                "issues": validation.issues,
                                "passed": validation.is_valid
                            })
                            
                            word_count = len(generated_content.split())
                            logger.info(f"      📊 Quality Score: {validation.quality_score:.1f}/{self.quality_threshold} | Words: {word_count} | Issues: {len(validation.issues)}")
                            
                            if validation.issues:
                                for issue in validation.issues:
                                    logger.debug(f"         ⚠️  {issue}")
                            
                            # If content passes QA, return it
                            if validation.is_valid:
                                logger.info(f"      ✅ Content APPROVED by QA")
                                metrics["model_used"] = f"Ollama - {model_name}"
                                metrics["final_quality_score"] = validation.quality_score
                                metrics["generation_time_seconds"] = time.time() - start_time
                                logger.info(f"\n{'='*80}")
                                logger.info(f"✅ GENERATION COMPLETE")
                                logger.info(f"   Model: {metrics['model_used']}")
                                logger.info(f"   Quality: {validation.quality_score:.1f}/{self.quality_threshold}")
                                logger.info(f"   Time: {metrics['generation_time_seconds']:.1f}s")
                                logger.info(f"{'='*80}\n")
                                return generated_content, metrics["model_used"], metrics
                            
                            # If content fails QA but we have refinement attempts left, try to improve
                            if metrics["refinement_attempts"] < self.max_refinement_attempts:
                                logger.info(f"      ⚙️  Content below threshold. Refining ({metrics['refinement_attempts'] + 1}/{self.max_refinement_attempts})...")
                                
                                metrics["refinement_attempts"] += 1
                                refinement_prompt = refinement_prompt_template.format(
                                    feedback=validation.feedback,
                                    issues="\n".join(validation.issues),
                                    content=generated_content
                                )
                                
                                # Try to refine with same model
                                response = await ollama.generate(
                                    prompt=refinement_prompt,
                                    system=system_prompt,
                                    model=model_name,
                                    stream=False,
                                )
                                
                                # Extract text from response dict
                                refined_content = ""
                                if isinstance(response, dict):
                                    refined_content = response.get("response", "")
                                    logger.debug(f"      📦 Refined response type: dict | Content: {len(refined_content)} chars")
                                elif isinstance(response, str):
                                    refined_content = response
                                    logger.debug(f"      📦 Refined response type: str | Content: {len(refined_content)} chars")
                                
                                if refined_content and len(refined_content) > 100:
                                    logger.info(f"      ✓ Refined content generated: {len(refined_content)} characters")
                                    
                                    # Validate refined content
                                    logger.info(f"      🔍 Validating refined content...")
                                    refined_validation = self._validate_content(refined_content, topic, target_length)
                                    metrics["validation_results"].append({
                                        "attempt": metrics["generation_attempts"],
                                        "refinement": metrics["refinement_attempts"],
                                        "score": refined_validation.quality_score,
                                        "issues": refined_validation.issues,
                                        "passed": refined_validation.is_valid
                                    })
                                    
                                    refined_word_count = len(refined_content.split())
                                    logger.info(f"      📊 Refined Quality: {refined_validation.quality_score:.1f}/{self.quality_threshold} | Words: {refined_word_count} | Issues: {len(refined_validation.issues)}")
                                    
                                    if refined_validation.is_valid:
                                        logger.info(f"      ✅ Refined content APPROVED")
                                        metrics["model_used"] = f"Ollama - {model_name} (refined)"
                                        metrics["final_quality_score"] = refined_validation.quality_score
                                        metrics["generation_time_seconds"] = time.time() - start_time
                                        logger.info(f"\n{'='*80}")
                                        logger.info(f"✅ GENERATION COMPLETE (with refinement)")
                                        logger.info(f"   Model: {metrics['model_used']}")
                                        logger.info(f"   Quality: {refined_validation.quality_score:.1f}/{self.quality_threshold}")
                                        logger.info(f"   Time: {metrics['generation_time_seconds']:.1f}s")
                                        logger.info(f"{'='*80}\n")
                                        return refined_content, metrics["model_used"], metrics
                                    
                                    generated_content = refined_content  # Use refined for next check
                            
                            # If still not passing after refinement, return best attempt
                            if metrics["generation_attempts"] == len(model_list):
                                logger.warning(f"      ⚠️  Content below quality threshold but no more refinements available")
                                metrics["model_used"] = f"Ollama - {model_name} (below threshold)"
                                metrics["final_quality_score"] = validation.quality_score
                                metrics["generation_time_seconds"] = time.time() - start_time
                                logger.info(f"\n{'='*80}")
                                logger.warning(f"⚠️  GENERATION COMPLETE (below quality threshold)")
                                logger.info(f"   Model: {metrics['model_used']}")
                                logger.info(f"   Quality: {validation.quality_score:.1f}/{self.quality_threshold}")
                                logger.info(f"   Time: {metrics['generation_time_seconds']:.1f}s")
                                logger.info(f"{'='*80}\n")
                                return generated_content, metrics["model_used"], metrics
                        else:
                            logger.warning(f"      ❌ Generated content too short or empty")
                    
                    except asyncio.TimeoutError as e:
                        # Explicitly catch timeout - model too slow or server unresponsive
                        error_msg = f"Timeout (120s exceeded) with {model_name}"
                        logger.warning(f"Ollama model {model_name} timed out: {error_msg}")
                        attempts.append(("Ollama", error_msg))
                        continue
                    except Exception as e:
                        # Catch other errors (500 errors, connection issues, etc.)
                        error_msg = str(e)[:150]  # Truncate long error messages
                        logger.warning(f"Ollama model {model_name} failed: {error_msg}")
                        attempts.append(("Ollama", f"{model_name}: {error_msg}"))
                        continue

            except Exception as e:
                logger.warning(f"Ollama generation failed: {e}")
                if not attempts:  # Only append if attempts list is still empty
                    attempts.append(("Ollama", str(e)[:150]))

        # 2. Try HuggingFace (free tier, online)
        if self.hf_token:
            logger.info("Attempting content generation with HuggingFace...")
            try:
                from .huggingface_client import HuggingFaceClient
                hf = HuggingFaceClient(api_token=self.hf_token)
                
                # Try recommended models
                for model_id in [
                    "mistralai/Mistral-7B-Instruct-v0.1",
                    "meta-llama/Llama-2-7b-chat",
                    "tiiuae/falcon-7b-instruct",
                ]:
                    try:
                        logger.debug(f"Trying HuggingFace model: {model_id}")
                        metrics["generation_attempts"] += 1
                        
                        generated_content = await asyncio.wait_for(
                            hf.generate(
                                model=model_id,
                                prompt=generation_prompt,
                                max_tokens=max(2000, target_length * 2),
                                temperature=0.7,
                            ),
                            timeout=60,
                        )
                        
                        if generated_content and len(generated_content) > 100:
                            # Self-check: Validate content quality
                            validation = self._validate_content(generated_content, topic, target_length)
                            metrics["validation_results"].append({
                                "attempt": metrics["generation_attempts"],
                                "score": validation.quality_score,
                                "issues": validation.issues,
                                "passed": validation.is_valid
                            })
                            
                            if validation.is_valid:
                                metrics["model_used"] = f"HuggingFace - {model_id.split('/')[-1]}"
                                metrics["final_quality_score"] = validation.quality_score
                                metrics["generation_time_seconds"] = time.time() - start_time
                                logger.info(f"✓ Content generated and approved with HuggingFace")
                                return generated_content, metrics["model_used"], metrics
                    
                    except asyncio.TimeoutError:
                        logger.debug(f"HuggingFace model {model_id} timed out")
                        continue
                    except Exception as e:
                        logger.debug(f"HuggingFace model {model_id} failed: {e}")
                        continue

            except Exception as e:
                logger.warning(f"HuggingFace generation failed: {e}")
                attempts.append(("HuggingFace", str(e)))

        # 3. Fall back to Google Gemini (paid, but reliable)
        if self.gemini_key:
            logger.info("Attempting content generation with Google Gemini (fallback)...")
            try:
                # Try the updated Gemini API format
                try:
                    import google.generativeai as genai
                    genai.configure(api_key=self.gemini_key)
                    model = genai.GenerativeModel("gemini-2.5-flash")
                    
                    metrics["generation_attempts"] += 1
                    response = model.generate_content(
                        f"{system_prompt}\n\n{generation_prompt}",
                        generation_config=genai.GenerationConfig(
                            max_output_tokens=max(2000, target_length * 2),
                            temperature=0.7,
                        ),
                    )
                    
                    generated_content = response.text
                    if generated_content and len(generated_content) > 100:
                        # Self-check: Validate content quality
                        validation = self._validate_content(generated_content, topic, target_length)
                        metrics["validation_results"].append({
                            "attempt": metrics["generation_attempts"],
                            "score": validation.quality_score,
                            "issues": validation.issues,
                            "passed": validation.is_valid
                        })
                        
                        metrics["model_used"] = "Google Gemini 2.5 Flash"
                        metrics["final_quality_score"] = validation.quality_score
                        metrics["generation_time_seconds"] = time.time() - start_time
                        logger.info(f"✓ Content generated with Gemini: {validation.feedback}")
                        return generated_content, metrics["model_used"], metrics
                        
                except (AttributeError, ImportError):
                    # Fallback for older SDK versions - try client API
                    logger.warning("Gemini SDK format not supported, attempting legacy API...")
                    attempts.append(("Gemini", "SDK version incompatible"))

            except ImportError:
                logger.warning("google.generativeai not installed, skipping Gemini")
            except Exception as e:
                logger.warning(f"Gemini generation failed: {e}")
                attempts.append(("Gemini", str(e)))

        # If all models fail, use fallback
        logger.error(f"All AI models failed. Attempts: {attempts}")
        fallback_content = self._generate_fallback_content(topic, style, tone, tags)
        metrics["model_used"] = "Fallback (no AI models available)"
        metrics["final_quality_score"] = 0.0
        metrics["generation_time_seconds"] = time.time() - start_time
        return fallback_content, metrics["model_used"], metrics

    def _generate_fallback_content(
        self,
        topic: str,
        style: str,
        tone: str,
        tags: list[str],
    ) -> str:
        """Generate fallback content when AI models are unavailable"""
        
        tag_str = ", ".join(tags) if tags else "general"
        
        return f"""# {topic}

## Introduction

{topic} is an important area that deserves careful attention and exploration. This article provides insights and practical considerations for understanding this topic better.

## Key Aspects

### Understanding the Basics

When exploring {topic}, it's essential to understand the foundational concepts. The {style} approach ensures clarity and depth, delivered in a {tone.lower()} manner that resonates with our audience.

### Practical Applications

{topic} has several real-world applications:

- **Strategic Implementation**: How to apply these concepts in practice
- **Common Challenges**: Obstacles to watch for and how to overcome them
- **Success Metrics**: How to measure progress and impact

### Emerging Trends

Looking ahead, {topic} continues to evolve:

- New opportunities and possibilities
- Challenges that may emerge
- Best practices for staying current

## Why This Matters

Understanding {topic} provides significant advantages:

1. Better decision-making capabilities
2. Improved efficiency and effectiveness
3. Competitive advantage in your field

## Practical Next Steps

Ready to dive deeper into {topic}?

1. **Learn More**: Research additional resources and case studies
2. **Experiment**: Try implementing one strategy this week
3. **Share**: Discuss what you've learned with colleagues
4. **Iterate**: Refine your approach based on results

## Conclusion

{topic} represents a valuable area for professional and personal development. By understanding the concepts and practices outlined in this article, you can make better decisions and achieve improved outcomes.

Take action today—the insights you gain will compound over time.

---

*Tags: {tag_str}*
*Last updated: {os.popen('date').read().strip()}*
"""


# Global instance
_generator: Optional[AIContentGenerator] = None


def get_content_generator() -> AIContentGenerator:
    """Get or create global content generator"""
    global _generator
    if _generator is None:
        _generator = AIContentGenerator()
    return _generator


async def test_generation():
    """Test content generation"""
    generator = get_content_generator()
    
    print("Testing content generation...")
    print(f"Ollama available: {generator.ollama_available}")
    print(f"HuggingFace token: {'✓' if generator.hf_token else '✗'}")
    print(f"Gemini key: {'✓' if generator.gemini_key else '✗'}")
    
    print("\nGenerating blog post...")
    content, model, metrics = await generator.generate_blog_post(
        topic="AI-Powered Content Creation for Modern Marketing",
        style="technical",
        tone="professional",
        target_length=1500,
        tags=["AI", "Marketing", "Technology"],
    )
    
    print(f"\nModel used: {model}")
    print(f"Content length: {len(content)} characters")
    print(f"Quality score: {metrics['final_quality_score']}/10")
    print(f"Generation attempts: {metrics['generation_attempts']}")
    print(f"Time taken: {metrics['generation_time_seconds']:.2f} seconds")
    print(f"\nFirst 500 characters:\n{content[:500]}...")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_generation())
