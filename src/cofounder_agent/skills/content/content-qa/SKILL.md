---
name: content-qa
description: >
  Content quality assurance — the adversarial QA gate pack. Topic-delivery,
  internal-consistency, publication-readiness review, aggregate rewrite,
  writer self-review for contradictions, self-consistency sampling, an
  LLM quality rubric, and vision-QA for inline images and rendered preview
  screenshots. Use after a draft exists, during the multi-model QA stage,
  to score, critique, and revise content before publication.
license: Apache-2.0
metadata:
  category: content_qa
  prompts:
    - key: qa.content_review
      output_format: json
      description: 'Default prompt — basic content quality review; production-quality prompt packs ship as a premium add-on'
    - key: qa.self_critique
      output_format: json
      description: 'Default prompt — basic self-critique; production-quality prompt packs ship as a premium add-on'
    - key: qa.topic_delivery
      output_format: json
      description: 'QA gate — does the body deliver on the topic the title promises (numeric, named entity, format, thesis checks)'
    - key: qa.consistency
      output_format: json
      description: 'QA gate — internal-contradiction check across recommendations, facts, principles, code-vs-prose'
    - key: qa.title_coherence
      output_format: json
      description: 'QA gate — does the TITLE honestly represent the article body (wrong-domain titles, directive/assignment-label leaks, unrecognizably generic titles). Complements qa.topic_delivery (body↔topic); this is title↔body.'
    - key: qa.person_mention.extract
      output_format: json
      description: 'Stage 1 of qa.person_mention — list the real people a passage names (few-shot; poindexter#1009)'
    - key: qa.person_mention.classify
      output_format: json
      description: 'Stage 2 of qa.person_mention — is ONE named person public-in-public-capacity, or a private individual?'
    - key: qa.review
      output_format: json
      description: 'QA gate — overall publication-readiness review (the third LLM critic; aggregates style/logic/coherence)'
    - key: qa.aggregate_rewrite
      output_format: text
      description: 'QA rewrite — feed every flagged issue (validator + LLM critics + consistency) into a single targeted rewrite, minimum changes, same structure'
    - key: qa.self_review.contradictions_review
      output_format: text
      description: "Writer-self-review pass — model reads its own draft and identifies cross-section contradictions before QA. Output: a numbered list of 'SECTION X conflicts with SECTION Y' lines, or exactly PASS when none found."
    - key: qa.self_review.contradictions_revise
      output_format: markdown
      description: 'Writer-self-review follow-up — given the contradiction list from qa.self_review.contradictions_review, fix only those specific issues without touching the rest of the draft.'
    - key: qa.self_consistency.summarize
      output_format: text
      description: "Self-consistency rail — produces a short article summary grounded strictly in the article body. Sampled N times at temperature>0; pairwise cosine similarity of the resulting summaries drives the rail's verdict."
    - key: qa.quality_evaluation_llm_rubric
      output_format: json
      description: 'LLM-based quality scoring — 7 dimensions (clarity, accuracy, completeness, relevance, seo_quality, readability, engagement) on 0-10 each. Replaces the inline f-string in quality_service.UnifiedQualityService when llm_client is wired.'
    - key: qa.vision_image_relevance
      output_format: json
      description: "Vision-QA: per-image relevance scoring. A vision-capable Ollama model rates each inline image 0-100 on how well it represents the article's subject, and separately estimates what share of the frame rendered text covers (always a defect — the generator is told to draw none). Used by MultiModelQA._check_image_relevance, which subtracts a proportional text penalty. Migrated from inline string 2026-05-28; text_coverage added 2026-09-23."
    - key: qa.vision_preview_screenshot
      output_format: json
      description: 'Vision-QA: full-page screenshot review. A vision-capable Ollama model rates the rendered preview 0-100 on layout, image rendering, and visual professionalism. Used by MultiModelQA.review_preview_screenshot. Migrated from inline string 2026-05-28.'
    - key: qa.video_shot_quality
      output_format: json
      description: 'Vision-QA: per-shot rendered-frame scoring (video-quality Piece 2 render-check loop). A vision-capable Ollama model rates one rendered video shot 0-100 on match-to-intent, on-brand palette, and usability. Used by services.video_renderers.shot_vision_qa.score_shot_frame.'
    - key: qa.video_stock_fit
      output_format: json
      description: 'Vision-QA for STOCK footage: does one frame of a Pexels clip fit the video topic and the narration spoken over it? Returns a fit label (fits / loose / off) and a 0-100 score; the label caps the score under the escalation threshold for loose and off. Used by services.video_renderers.shot_vision_qa.score_shot_frame for source=pexels.'
    - key: qa.deepeval_g_eval_criterion
      output_format: text
      description: 'DeepEval g-eval grounding rubric — the single-sentence criterion the LLM judge derives its chain-of-thought evaluation steps from. Used by services.deepeval_rails.evaluate_g_eval; the seeded app_settings.deepeval_g_eval_criterion operator override still wins when set.'
    - key: qa.featured_image_fanout
      output_format: json
      description: 'Vision judge for the featured-image fan-out (Phase 1): scores ONE candidate image 0-100 against the featured-image brief on adherence, composition, brand fit, artifact-freedom, and absence of legible text. Used by services.image_fanout._score_candidate; highest score across candidates ships.'
---

# Content QA skill

The adversarial quality-assurance pack — the QA moat. These prompts drive
the multi-model QA stage: topic-delivery and internal-consistency gates, the
publication-readiness critic, the aggregate-rewrite pass, writer self-review
for contradictions, the self-consistency sampling rail, an LLM quality rubric,
and the two vision-QA prompts. The architect routes on the `description` above;
`UnifiedPromptManager` resolves each template by `key` (Langfuse override still
wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## qa.content_review

```text
Review this content for quality. Return JSON with keys: score (1-10), issues (list), suggestions (list).
Content: {content}
```

## qa.self_critique

```text
Self-critique this content. Return JSON with keys: strengths (list), weaknesses (list), improvements (list).
Content: {content}
```

## qa.topic_delivery

```text
You are a strict editor checking whether an article
delivers on its topic. A reader clicking this article expects what the topic
promises. Did the writer deliver?

REQUESTED TOPIC: {topic}

ARTICLE OPENING (first ~1000 words):
{opening}

Check these specific failure modes:

  1. Numeric promises. If the topic says "10 X" or "11 Y" or "5 Z", does the
     body actually list that many? Partial lists (two items then a pivot to
     generalities) FAIL.
  2. Named entities. If the topic names a specific product, person, or
     technology ("Llama 4", "Claude", "indie hackers making $1M+"), does the
     body actually discuss that specific thing? An article titled "Llama 4"
     that only discusses Llama 3.1 FAILS.
  3. Format promise. If the topic implies a guide, tutorial, list, or review,
     does the body deliver that format? A "guide" that's actually an opinion
     piece FAILS.
  4. Angle/thesis. Is the article's thesis actually about the topic, or did
     the writer pivot to a tangential point they preferred?

Respond with ONLY valid JSON:
{{"delivers": true/false, "score": NUMBER 0-100, "reason": "concise — name the specific gap when one exists"}}

Scoring guidance: delivers=true and score 85-100 if the body is a faithful
execution of the topic. delivers=false and score 0-40 if the body is a
bait-and-switch or numeric underdelivery or misnamed version. delivers=true
and score 60-80 if the body is mostly on-topic but weaker than the topic
implies.
```

## qa.consistency

```text
You are a strict editor checking an article for
internal contradictions. Readers lose trust when section 1 says X and
section 3 says not-X, even if both are defensible on their own.

ARTICLE (full text):
{content}

Read the entire article and look for:

  1. Recommendation contradictions. Does one section recommend tool/approach
     A and another section recommend incompatible tool/approach B without
     acknowledging the switch? ("Don't use React" followed by "use Next.js"
     is a contradiction; Next.js is React.)
  2. Factual contradictions. Does one section state a number, version, or
     claim that another section directly contradicts?
  3. Principle contradictions. Does the article lay out a principle in one
     section ("never build custom auth") and then show code that violates it
     in another section?
  4. Code vs prose contradictions. Does the prose claim the code does X when
     the code actually does Y? (e.g. "the code validates the referrer" when
     the code just inserts without validating.)

Respond with ONLY valid JSON:
{{"consistent": true/false, "score": NUMBER 0-100, "contradictions": ["list","of","specific","pairs"]}}

Scoring guidance: consistent=true and score 85-100 if no contradictions.
consistent=false and score 0-50 if one or more contradictions found.
Be specific in the contradictions list — name the sections and the conflict.
```

## qa.title_coherence

```text
You are reviewing a blog post before publication. Judge whether the TITLE
honestly represents the ARTICLE a reader will get.

A title FAILS when:
- it promises subject matter the article does not deliver (wrong domain, wrong
  product, wrong audience — e.g. a gaming-hardware title on a software essay)
- it is an internal instruction, assignment label, or directive rather than a
  headline (e.g. "Expand coverage of X")
- it is so generic it could top thousands of unrelated articles and names
  nothing recognizable from this one

A title PASSES when a reader who finishes the article would agree the title
described it — abstract, playful, or metaphorical titles pass as long as the
article cashes them in.

Return ONLY a JSON object, no other text:
{{"title_represents_article": true or false, "confidence": <integer 0-100>, "reason": "<one sentence>"}}

TITLE: {title}

ARTICLE:
{content}
```

## qa.person_mention.extract

```text
List the real people this passage names.

Include every human being referred to by name: an author, a researcher, an
executive, a journalist, a reviewer, a business owner, someone mentioned only
in passing. Include a person named by surname alone when the passage clearly
refers to a specific human.

Do not include companies, products, tools, model names, places, teams, or
generic stand-ins in an example ("Alice sends Bob a message"). A capitalised
phrase is not a person just because it is capitalised.

Example 1
PASSAGE: Ray Dalio's Principles argues for radical transparency, and the
culture it describes has been debated ever since. We reached a similar
conclusion running our own pipeline on an RTX 5090, where Grafana made the
tradeoff visible.
ANSWER: {{"people": ["Ray Dalio"]}}

Example 2
PASSAGE: The retention job prunes checkpoint rows older than thirty days.
Grafana renders the backlog per handler, and the probe pages when a policy
stops deleting rows entirely.
ANSWER: {{"people": []}}

Now the real passage.

PASSAGE:
{content}

ANSWER:
```

## qa.person_mention.classify

```text
A commercial blog post names the person below. Decide what kind of person
they are, using only how the article itself uses them.

PERSON: {person}

HOW THE ARTICLE USES THEM:
{context}

Answer "public" when they are a public figure appearing in their public
capacity — an author and their book, a researcher and their paper, an
executive and their public statement, a historical figure, a journalist and
their reporting.

Answer "private" when they are a private individual — someone who merely
shares a name or a location with the topic, a named customer or reviewer, a
local business owner, a non-spokesperson employee, or anyone whose presence
here is incidental rather than the subject of their public work.

Return ONLY a JSON object, no other text:
{{"status": "public" or "private", "confidence": <integer 0-100>, "reason": "<one sentence>"}}
```

## qa.review

```text
Review this blog post for publication readiness. Be critical but fair.

TODAY'S DATE: {current_date}

TITLE: {title}
TOPIC: {topic}

{sources_block}---CONTENT---
{content}
---END---

Evaluate:
1. Is the content factually accurate? Flag any claims that seem fabricated.
2. Is the writing clear, engaging, and well-structured?
3. Are there any hallucinated people, statistics, or quotes?
4. Would this be valuable to the target audience (developers and founders)?
5. Is this FINISHED ARTICLE PROSE from the first line to the last? Planning
   notes, outlines, bullet-point drafting scaffolds, echoed writing
   instructions, or notes-to-self ("I should add...", "Check word count")
   are never publishable — whether they make up the whole text or only
   open it before the article begins.

REVIEW WINDOW: if the CONTENT ends with a "[REVIEW EXCERPT ENDS ...]"
marker, you are seeing the opening portion of a longer article that
continues past the marker. Judge only the text shown. The excerpt
boundary is NOT an unfinished or truncated ending — never reject or
lower the score because the excerpt stops there.

UNFINISHED CONTENT IS AN AUTOMATIC REJECT:

When any part of the CONTENT is a plan, outline, instruction echo, or
drafting scaffold rather than finished prose, set approved=false and cap
quality_score at 25. Score only the text actually on the page — never
the article the plan describes or the title promises. A well-organized
outline is still a reject: readers must never see it.

GROUND YOUR REVIEW IN THE CONTENT:

Quote a short phrase from the CONTENT for anything you praise or
criticize. If you cannot point to text that supports a judgment, do not
make that judgment.

FEEDBACK MUST BE ACTIONABLE WITHOUT INVENTING MATERIAL:

Your feedback drives an automated revision pass that can only rework
what is already on the page or in SOURCES. Never ask the writer to add
anecdotes, quotes from industry experts, case studies, statistics, or
citations that are absent from the CONTENT and SOURCES — demands like
those push the reviser toward fabrication. When a section feels thin,
either name the specific on-page or in-SOURCES material to expand, or
lower the score for lack of depth and say so plainly, without
prescribing additions that would have to be invented.

HANDLING CLAIMS YOU DO NOT RECOGNIZE:

Your training data has a cutoff. The article may cover hardware,
software, or events released after your cutoff but before today's
date above. Apply this rubric to claims about products, versions,
frameworks, or events you do not personally recognize:

  - Treat "I have not heard of this" as "outside my knowledge",
    distinct from "this is fabricated". A name being unfamiliar
    is signal but not proof.
  - Reject as fabricated when claims are internally contradictory,
    suspiciously specific (fake-looking statistics, quotes attributed
    to real people, made-up studies with impossible citations), or
    physically/logically impossible.
  - Mark as "uncertain — cannot verify" when the claim is plausible
    for today's date but outside your knowledge, and lower the score
    modestly for that unverifiable specificity.
  - Reject outright the universal failure modes regardless of date:
    fabricated people, fake statistics, invented quotes.
  - Accept claims that fall outside your knowledge but match common
    industry patterns (a startup with a real-looking product page, a
    library with a plausible API, a metric within typical ranges).

USING THE SOURCES SECTION (when present above):

The SOURCES block contains the research corpus the writer consulted
while drafting this post — real links, pulled excerpts, internal
reference material. Treat it as authoritative ground truth for this
specific article. For each factual claim:

  - When the claim appears in or is supported by the SOURCES, accept
    it as grounded. This holds even when the claim falls outside
    your training knowledge.
  - When the claim is absent from SOURCES and outside your knowledge,
    flag it as "unverified — not backed by provided research" and
    lower the score modestly. Reject only when the claim is
    additionally implausible.
  - When the claim contradicts the SOURCES, reject it.
  - Common knowledge ("HTTP uses status codes", "Postgres supports
    JSONB") passes without needing a SOURCES entry.

When the SOURCES block is absent, evaluate from your training
knowledge using the cutoff rubric above.

Output one JSON object. The first character is `{{` and the last
character is `}}`:
{{"approved": true/false, "quality_score": NUMBER 0-100, "feedback": "concise — name what's strong and what needs revision"}}
```

## qa.aggregate_rewrite

```text
You are revising your own draft to fix
EVERY issue a team of editors identified. Do NOT rewrite the entire
article. Do NOT add new sections. Only fix the specific problems
listed below, making the minimum changes needed to resolve each one.

Keep the same structure, same headings, same code examples where they
aren't affected by the issues, same length (within 10%).

TITLE: {title}

ISSUES TO FIX (from programmatic validator + LLM critics + consistency checker):
{issues_to_fix}

How to interpret:
- "[critical]" means the issue will block publishing if not fixed. Top priority.
- "[warning]" means it will drag the score down but won't veto. Fix these too.
- "Contradictions:" lines mean sections disagree with each other — rewrite the
  weaker or later one to align with the stronger or earlier one.
- "Fabricated" or "Impossible" lines mean the draft made up a person, statistic,
  quote, or company claim. Remove the fabrication entirely; do NOT replace it
  with another made-up fact — either soften to a general statement or cut.
- "Generic section title" means replace the heading with a creative, benefit-
  focused alternative (never "Introduction", "Conclusion", "Summary", etc.).
- "Filler intro" means rewrite the first paragraph with a concrete hook, not
  "In this post..." or "In today's fast-paced world...".

ORIGINAL DRAFT:
{content}

Return ONLY the revised article text. Do not include meta-commentary,
notes about what you changed, or markdown code fences around the output.
```

## qa.self_review.contradictions_review

```text
You are reviewing your own draft for internal contradictions.

TITLE: {title}
TOPIC: {topic}

DRAFT:
{draft}

Read every section. Identify any claim in one section that contradicts a claim, code example, or recommendation in another section. Ignore stylistic variation; focus on factual or logical conflicts.

If you find contradictions, output a numbered list of specific corrections needed (one per line, format: 'SECTION X conflicts with SECTION Y: <details>'). If you find none, reply with exactly: PASS
```

## qa.self_review.contradictions_revise

```text
Here is your draft. Fix these specific contradictions and nothing else:

CONTRADICTIONS TO FIX:
{review_text}

ORIGINAL DRAFT:
{draft}

Output only the revised draft. Keep the structure, length, and tone identical. Only change what's needed to resolve the contradictions. Preserve any [IMAGE: ...], [IMAGE-N: ...], and [HERO-IMAGE: ...] markers exactly as they appear — do not remove, renumber, reword, or relocate them.
```

## qa.self_consistency.summarize

```text
Summarize the following article in two sentences. Stay strictly grounded in the article — do not introduce facts that aren't explicitly stated. Output only the summary, no preamble.

Article topic: {topic}

Article:
{content}

Summary:
```

## qa.quality_evaluation_llm_rubric

```text
You are a content quality evaluator. Score the following content on 7 dimensions, each from 0 to 10 (integers only). Respond ONLY with a JSON object — no markdown, no explanation.

Topic: {topic}

Content:
{content_excerpt}

Return JSON with these keys:
{{"clarity": N, "accuracy": N, "completeness": N, "relevance": N, "seo_quality": N, "readability": N, "engagement": N, "feedback": "one sentence summary", "suggestions": ["suggestion1", "suggestion2"]}}
```

## qa.vision_image_relevance

```text
You are reviewing images attached to a blog post. Report TWO independent
judgements for EACH image attached, in attachment order.

TITLE: {title}
TOPIC: {topic}

ARTICLE SNIPPET:
{content_snippet}

1. RELEVANCE, 0-100: how well the image represents the article's subject and
   would help a reader understand the content. A generic stock photo with no
   connection to the topic scores below 50. An image that directly illustrates
   a concept from the article scores 80+. Judge the PICTURE - its subject,
   setting and action. Lettering in the frame is never evidence that an image
   is on-topic: these images are generated with an explicit instruction to
   render no text at all, so a headline, caption, label or signature is an
   artifact the generator was told not to draw, not a title anyone chose.
   Never cite words you can see in the image as a reason for a high score.

2. TEXT_COVERAGE, 0-100: the percentage of the image area taken up by rendered
   text. Count BOTH readable words or digits AND garbled pseudo-text that only
   resembles writing - unreadable letter-like squiggles, fake glyph rows
   standing in for body copy, nonsense words. Both are defects here, because
   the generator was told to produce neither. Estimate the share of the frame
   that the lettering and its block occupy: 0 for a clean frame, a few percent
   for a small mark in one corner, 30 or more when a banner headline or a block
   of fake copy fills a band of the image.

Respond with ONLY valid JSON. Both arrays must have one entry per attached
image, in the same order:
{{"scores": [int,...], "text_coverage": [int,...], "reasons": ["short reason per image", ...], "overall": int}}
```

## qa.vision_preview_screenshot

```text
You are the final visual reviewer for a blog post before it goes live. The attached image is a full-page screenshot of the post as it will appear to readers.

TITLE: {title}
TOPIC: {topic}

Rate 0-100 how professional and readable the rendered page looks. Deductions for:
  - Broken or missing images (placeholder icons, alt text showing)
  - Layout problems (overflowing tables, code blocks spilling outside the container)
  - Empty or near-empty sections
  - Mangled HTML (raw tags visible, unclosed quotes, escaped entities)
  - Visually unbalanced pages (a wall of text with no breaks)
  - Anything that would make a reader bounce in 3 seconds

A clean, professional post scores 80+. A post with any ONE serious visual defect scores below 60.

Respond with ONLY valid JSON:
{{"score": int, "approved": true/false, "issues": ["specific visual problem 1", ...]}}
```

## qa.video_shot_quality

```text
You are a video-quality reviewer scoring a SINGLE rendered shot from a Glad Labs
explainer video. Judge only this one image as this one shot.

SHOT INTENT (why this shot exists): {intent}
SHOT SUBJECT (what it should show): {visual}
SHOT SOURCE: {source}

Two questions, in this order:

1. ON TOPIC - does this shot serve its intent? Would a viewer understand why it
   is on screen at this moment? A shot that is well made but about something
   else is the worst outcome here.

2. LOOKS GOOD - is this an attractive, well-made frame? Judge composition,
   lighting, clarity and finish, the way you would judge a stock clip you were
   deciding whether to licence.
   People, faces, hands and text are all WELCOME and are never defects in
   themselves. Judge whether they are rendered WELL:
   - a person who looks real and natural is a good shot; melted features,
     extra fingers or warped anatomy are not
   - text that reads as real words is a good shot; text smeared into
     letter-like shapes is not. Judge only text the shot MEANS for a viewer to
     read - a screen of logs, a caption, a label. Incidental lettering that
     would be unreadable in real life too (a number plate at speed, a distant
     sign, a blurred logo) is scenery, not a fault. Read text that matters
     letter by letter; if it is too small or blurred to read, answer
     "unverifiable" - never assume it is correct, never invent what it says
   - the house look is dark-techno (deep navy, cyan, teal, gold) and stylized
     rather than photoreal for AI-rendered shots; real stock footage is exempt

Name a fault only when you can point at it in this image. A shot with nothing
wrong scores high - do not hunt for faults to justify a lower number.

Score bands:
- 85-100 on topic and good-looking; nothing worth naming
- 60-84  on topic and usable, one cosmetic nit
- 30-59  a fault a viewer would notice - visible warping, garbled text, muddy
         or ugly rendering, or only loosely related to the intent
- 0-29   off topic, or so mangled it reads as slop

Output EXACTLY one JSON object, no prose, no code fences:
{{"defects": ["<short phrase>", ...], "score": <integer 0-100>, "reason": "<one short sentence>"}}
```

## qa.video_stock_fit

```text
You are checking one frame of REAL STOCK FOOTAGE chosen for a moment in a
Glad Labs explainer video. Stock is found by search, so the search words only
record how the clip was found. Judge the picture against the video and the
words being spoken.

VIDEO TOPIC: {topic}
NARRATION WHILE THIS CLIP IS ON SCREEN: "{narration}"
WHY THE DIRECTOR WANTED A SHOT HERE: {intent}
SEARCH WORDS USED TO FIND IT: {visual}

1. FIT - would a viewer watching a video about the VIDEO TOPIC, hearing the
   NARRATION, see why this footage is on screen?
   - FITS: it shows what the narration is about, a concrete thing the narration
     names, or the setting that work happens in (hardware, a data centre, a
     person at a workstation, for a sentence about computing). Stock is expected
     to illustrate like this; it does not have to show the exact idea.
   - LOOSE: the link needs explaining - decorative light, texture or noise
     standing in for an idea.
   - OFF: a different subject, or readable text or interface on screen that is
     about something other than this video.

2. LOOKS GOOD - composition, lighting, focus and finish, as if you were
   deciding whether to licence the clip. A black, blank or empty frame reads as
   broken.

Score bands:
- 85-100 fits, and looks good
- 60-84  fits, with one cosmetic nit
- 30-59  loose, or a fault a viewer would notice
- 0-29   off, or a black / blank / broken frame

Output EXACTLY one JSON object, no prose, no code fences:
{{"fit": "fits|loose|off", "defects": ["<short phrase>", ...], "score": <integer 0-100>, "reason": "<one short sentence>"}}
```

## qa.deepeval_g_eval_criterion

```text
The output is well-grounded in the input topic, internally consistent across paragraphs, and does not invent specific facts, names, statistics, or quotes that lack support.
```

## qa.featured_image_fanout

```text
You are judging ONE candidate for a blog post's featured (hero) image. Several
models rendered the same brief; each candidate is scored separately and the
highest score ships. Judge only the image you are shown.

THE BRIEF (the prompt the image was rendered from): {brief}

Score this candidate 0-100:
- ADHERENCE - does the image actually depict what the brief asks for, including
  the requested style (isometric / flat vector / cinematic / etc.)?
- COMPOSITION - clear focal subject, balanced framing, works as a wide hero
  image at a glance.
- CLEAN - no warped geometry, no melted or extra limbs, no AI-slop artifacts.
- TEXT - judge text by whether a reader could READ it, not by whether marks
  look text-shaped. Apply exactly one of:
  - READABLE WORDS OR DIGITS a reader could transcribe - a title, a label, a
    caption, numerals on a dial or scale, characters in any script. Caps the
    score at 40, however small, however few, however well the image is
    otherwise made.
  - GARBLED PSEUDO-TEXT that only resembles writing - unreadable squiggles,
    fake glyph rows standing in for body copy, decorative marks. Subtract 10
    to 20 points. Do NOT cap: this is an artifact, not text.
  - NO TEXT-LIKE MARKS AT ALL. No deduction.
  Judge only what is in THIS image. If the brief asks for something
  text-adjacent (code fragments, blueprint annotations, an open book), the
  same test still decides it: readable caps, unreadable deducts.
- BRAND - stylized illustration energy over photorealism; rich, confident
  palette.

Output EXACTLY one JSON object, no prose, no code fences:
{{"score": <integer 0-100>, "reason": "<one short sentence>"}}
```
