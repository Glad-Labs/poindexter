---
name: image-generation
description: >
  Image-direction prompts for the content pipeline. Generate featured-image
  prompts, build stock image search queries, and run the image-director
  reasoning that picks which sections get visuals (image_gen vs pexels, style,
  and prompt/query for each) plus one featured hero image.
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
---

# Image generation skill

Three prompts the pipeline uses to direct visuals for a post — a featured
hero prompt, stock search queries, and the image-director reasoning that
decides which sections get images. The architect routes on the `description`
above; `UnifiedPromptManager` resolves each template by `key` (Langfuse
override still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## image.featured_image

```text
Write a single Stable Diffusion XL image prompt for a magazine-style editorial cover illustration.

Article topic: {topic}
Art style: {style} — {style_tags}

Depict a concrete, specific subject or scene that represents the topic (a recognizable object, place, or visual metaphor), rendered fully in the "{style}" art style. Commit to that style's medium, palette, and composition. Do NOT default to a generic glowing-circuit board or abstract floating-data backdrop, and do not lock every image to teal/cyan — vary the focal subject, composition, and color treatment so it reads differently from a typical tech illustration. Faceless silhouettes are fine; no identifiable faces, no hands, no text or words in the image.

Output ONLY the image prompt, 1-2 sentences, nothing else.
```

## image.inline_illustration

```text
Write a single Stable Diffusion XL image prompt for a blog section illustration.

Section subject: {search_query}
Article topic: {topic}
Art style: {style}

Depict a specific, concrete scene for the section subject, rendered fully in the "{style}" art style — commit to that style's medium and palette rather than a generic tech render or screenshot. No people, no identifiable faces, no hands, no text or words. Vary the composition so it doesn't look like every other section image.

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

SECTIONS:
{section_list}

AVAILABLE IMAGE SOURCES:
- "image_gen": AI-generated images. Best for: abstract concepts, mood imagery, artistic visualizations, diagrams, conceptual scenes. Styles: blueprint, dramatic, minimal, isometric, macro, editorial.
- "pexels": Stock photography. Best for: real-world objects, environments, workspaces, hardware close-ups, materials. Avoid shots of people.

RULES:
1. Pick {max_images} sections that would benefit most from a visual (skip sections that are mostly code)
2. For each, decide: image_gen or pexels? What style? What specific image?
3. Also decide on 1 featured image (the hero/header image for the article)
4. Be specific in your prompts — describe the exact scene, not vague concepts
5. NEVER depict people, hands, faces, or human figures in ANY image — the brand style is objects, hardware, and environments only. Also never put text, words, or letters in AI-generated images.

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
