---
name: image-generation
description: >
  Image-direction prompts for the content pipeline. Generate featured-image
  prompts, build stock image search queries, run the image-director
  reasoning that picks which sections get visuals (image_gen vs pexels, style,
  and prompt/query for each) plus one featured hero image, and caption
  rendered images with vision-derived alt text.
license: Apache-2.0
metadata:
  category: image_generation
  prompts:
    - key: image.featured_image
      output_format: text
      description: 'Generate a featured-image prompt for a blog post topic in a chosen art style'
    - key: image.inline_illustration
      output_format: text
      description: 'Generate an inline section-illustration prompt in a chosen art style'
    - key: image.search_queries
      output_format: json
      description: 'Generate stock-image search queries for a topic'
    - key: image.decision
      output_format: json
      description: 'Image-director reasoning prompt — picks which sections get visuals, image_gen vs pexels, style, and prompt/query for each, plus one featured hero image'
    - key: image.caption_alt_text
      output_format: text
      description: 'Vision alt-text instruction — describe only what is actually visible in the image, one factual sentence under {budget} characters. Used by services/image_captioner.py (the caption_images stage + the alt-text backfill script).'
---

# Image generation skill

The prompts the pipeline uses to direct visuals for a post — a featured
hero prompt, inline-section illustrations, stock search queries, the
image-director reasoning that decides which sections get images, and the
vision alt-text captioner that describes the rendered result. The architect
routes on the `description` above; `UnifiedPromptManager` resolves each
template by `key` (Langfuse override still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## image.featured_image

```text
Write a single Stable Diffusion XL image prompt for a magazine-style editorial cover illustration.

Image subject: {subject}
Art style: {style} — {style_tags}

Depict a concrete, specific subject or scene that represents the image subject above (a recognizable object, place, or visual metaphor), rendered fully in the "{style}" art style. Commit to that style's medium, palette, and composition. Do NOT default to a generic glowing-circuit board or abstract floating-data backdrop, and do not lock every image to teal/cyan — vary the focal subject, composition, and color treatment so it reads differently from a typical tech illustration. People are welcome when they serve the subject — keep them stylized (never photoreal) and their action simple and specific; one or two figures beat a crowd. No text or words in the image.

Output ONLY the image prompt, 1-2 sentences, nothing else.
```

## image.inline_illustration

```text
Write a single Stable Diffusion XL image prompt for a blog section illustration.

Section subject: {search_query}
Art style: {style}

Depict a specific, concrete scene for the section subject, rendered fully in the "{style}" art style — commit to that style's medium and palette rather than a generic tech render or screenshot. People are fine when the section subject involves them — stylized, never photoreal, doing something concrete. No text or words. Vary the composition so it doesn't look like every other section image.

Output ONLY the image prompt, 1 sentence, nothing else.
```

## image.search_queries

```text
Generate 3 image search queries as JSON array for the topic: {topic}
```

## image.decision

```text
You are an image director for a {category} content site. Analyze this article and decide what images would make it more engaging.

ARTICLE TOPIC: {topic}
CATEGORY: {category}

SECTIONS (title + a short excerpt of the actual text):
{section_list}

AVAILABLE IMAGE SOURCES:
- "image_gen": AI-generated images. Best for: abstract concepts, mood imagery, artistic visualizations, conceptual scenes. Styles: blueprint, dramatic, minimal, isometric, macro, editorial.
- "pexels": Stock photography. Best for: a recognisable real place, product, or event a render cannot fake, and documentary texture where the POINT is that it's real. Prefer a generated image otherwise — generic stock reads as filler.

RULES:
1. Pick {max_images} sections that would benefit most from a visual (skip sections that are mostly code)
2. For each, decide: image_gen or pexels? What style? What specific image?
3. Also decide on 1 featured image (the hero/header image for the article)
4. Be specific in your prompts — describe the exact scene, not vague concepts
5. People are permitted and often clearer than a metaphor — render them STYLIZED (never photorealistic), one or two figures, doing something concrete and relevant. (This reverses an older blanket ban from when diffusion models produced melted faces and hands; re-verified 2026-08-27 against the current model.) Never put text, words, or letters in AI-generated images — and never ask for a "diagram" or "chart" specifically, since diffusion models render those with garbled fake labels; describe the underlying object or scene instead.
6. Ground each image's subject in the section's excerpt — depict what that section actually discusses, not a generic {category} image.

Output ONLY valid JSON (no markdown, no explanation):
{{
  "featured": {{
    "source": "image_gen" or "pexels",
    "style": "style_name",
    "prompt": "detailed image prompt or search query",
    "reasoning": "why this image works for the hero"
  }},
  "inline": [
    {{
      "section": "exact section title",
      "source": "image_gen" or "pexels",
      "style": "style_name",
      "prompt": "detailed image prompt or search query",
      "reasoning": "why this visual helps this section"
    }}
  ]
}}
```

## image.caption_alt_text

```text
Write alt text for this image. Describe ONLY what is actually visible — factual, concise, one sentence, under {budget} characters. Do NOT begin with 'image of' or 'photo of'. Do NOT invent details that aren't visible.
```
