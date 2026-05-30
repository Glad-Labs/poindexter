---
name: image-generation
description: >
  Image-direction prompts for the content pipeline. Generate featured-image
  prompts, build stock image search queries, and run the image-director
  reasoning that picks which sections get visuals (sdxl vs pexels, style,
  and prompt/query for each) plus one featured hero image.
license: Apache-2.0
metadata:
  category: image_generation
  prompts:
    - key: image.featured_image
      output_format: text
      description: 'Generate a featured-image prompt for a blog post topic'
    - key: image.search_queries
      output_format: json
      description: 'Generate stock-image search queries for a topic'
    - key: image.decision
      output_format: json
      description: 'Image-director reasoning prompt — picks which sections get visuals, sdxl vs pexels, style, and prompt/query for each, plus one featured hero image'
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
Generate an image prompt for a featured blog image about: {topic}
Style: professional, modern, relevant to the content.
```

## image.search_queries

```text
Generate 3 image search queries as JSON array for the topic: {topic}
```

## image.decision

```text
You are an image director for a tech blog. Analyze this article and decide what images would make it more engaging.

ARTICLE TOPIC: {topic}
CATEGORY: {category}

SECTIONS:
{section_list}

AVAILABLE IMAGE SOURCES:
- "sdxl": AI-generated images. Best for: abstract concepts, mood imagery, artistic visualizations, diagrams, futuristic scenes. Styles: blueprint, dramatic, minimal, isometric, macro, editorial.
- "pexels": Stock photography. Best for: real-world objects, hardware close-ups, workspaces, screens with code, servers, people working (if appropriate).

RULES:
1. Pick {max_images} sections that would benefit most from a visual (skip sections that are mostly code)
2. For each, decide: sdxl or pexels? What style? What specific image?
3. Also decide on 1 featured image (the hero/header image for the article)
4. Be specific in your prompts — describe the exact scene, not vague concepts
5. NEVER include text, words, letters, or faces in SDXL images

Output ONLY valid JSON (no markdown, no explanation):
{{
  "featured": {{
    "source": "sdxl" or "pexels",
    "style": "style_name",
    "prompt": "detailed image prompt or search query",
    "reasoning": "why this image works for the hero"
  }},
  "inline": [
    {{
      "section": "exact section title",
      "source": "sdxl" or "pexels",
      "style": "style_name",
      "prompt": "detailed image prompt or search query",
      "reasoning": "why this visual helps this section"
    }}
  ]
}}
```
