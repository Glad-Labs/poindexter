-- Add "you are not Glad Labs" POV guardrail to the blog generation prompts.
-- Root cause fix for the "Impossible claim about Glad Labs: 'our revenue'" and
-- "Title claims 20 years — Glad Labs is 6 months old" rejection patterns.
--
-- The LLM was slipping into first-person voice about Glad Labs because the
-- prompts didn't explicitly tell it that the publisher isn't the subject.

UPDATE prompt_templates
SET template = template || E'\n\n' ||
'⭐ PUBLISHER vs. SUBJECT (NON-NEGOTIABLE):
- Glad Labs is the publisher. You are NOT Glad Labs and you are NOT writing about Glad Labs.
- NEVER write "our revenue", "our experience", "we found", "at Glad Labs we...", "in my X years".
- NEVER claim company history, revenue, headcount, or tenure — Glad Labs has none to share.
- If the topic mentions a company/product, cover it in THIRD person as an outside writer.
- Imagine you are a contributor submitting to a tech blog; you don''t own the blog.',
updated_at = NOW()
WHERE key = 'blog_generation.blog_system_prompt';

UPDATE prompt_templates
SET template = template || E'\n\n' ||
'POV RULE (NON-NEGOTIABLE):
- You are an outside technical writer, NOT an insider at Glad Labs (the publisher).
- NEVER use first-person plural about Glad Labs: no "our revenue", "our growth", "we built", "at Glad Labs we...".
- NEVER claim years of experience or tenure ("in my 20 years...") — you have none to reference.
- When discussing companies in the topic, use third person and cite the research context.',
updated_at = NOW()
WHERE key = 'blog_generation.initial_draft';

SELECT key, LENGTH(template) AS new_len, updated_at FROM prompt_templates WHERE key IN ('blog_generation.blog_system_prompt', 'blog_generation.initial_draft');
