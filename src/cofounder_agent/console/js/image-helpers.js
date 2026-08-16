/* ══════════════════════════════════════════════════════════════
   Draft-image helpers — pure functions (window.PXImages)
   ──────────────────────────────────────────────────────────────
   Plain-JS, no React: parse a draft body into the image list the
   approve drawer's Images action renders (thumbnail strip + regen
   targets). Kept out of drawer.jsx (kpis.js / schedule-helpers.js
   pattern) so the node:test + vm suite exercises the REAL logic
   with no DOM.

   The invariant that matters: `inline:N` here MUST mean the same
   image the server will rewrite. PostEditService numbers inline
   images 1-based over the src-carrying <img> tags of the LATEST
   pipeline_versions row (_IMG_TAG_RE, post_edit_service.py), and
   GET /api/tasks/{id} serves content from that same max-version
   row (the content_tasks view) — so parsing that content with the
   same regex semantics keeps console numbering and server
   numbering in lockstep. If the server regex changes, change
   IMG_SRC_RE with it.
   ══════════════════════════════════════════════════════════════ */
(function () {
  // EXACT mirror of _IMG_TAG_RE: <img …src="…", case-insensitive,
  // non-greedy up to the FIRST src attribute — deliberately no closing-`>`
  // requirement, so a tag the server would count, we count. Only
  // src-carrying tags number toward N (same set _IMG_TAG_FULL_RE matches
  // for every body this pipeline generates — a generated <img> always
  // carries src).
  const IMG_SRC_RE = /<img\b[^>]*?\bsrc="([^"]*)"/gi;
  const ALT_RE = /\balt="([^"]*)"/i;

  // Mirrors _HEADING_RE (## … ####) + _BOLD_HEADING_RE (**pseudo-heading**
  // alone on a line, ≤80 chars). Heading text doubles as the regen-prompt
  // prefill — image prompts come from headings, not body prose.
  const HEADING_RE = /^#{2,4}\s+(.+)$/gm;
  const BOLD_HEADING_RE = /^\*\*(.{1,80}?)\*\*\s*$/gm;

  // Nearest heading ENDING before `pos`, scanning both patterns —
  // mirrors _find_preceding_heading (best match by end offset).
  function precedingHeading(content, pos) {
    let bestEnd = -1;
    let bestText = null;
    for (const re of [HEADING_RE, BOLD_HEADING_RE]) {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(content)) !== null) {
        const end = m.index + m[0].length;
        if (end > pos) break;
        if (end > bestEnd) {
          bestEnd = end;
          bestText = m[1].trim();
        }
      }
    }
    return bestText;
  }

  // Parse a draft into the drawer's image list. Featured first (when set),
  // then inline images in body order — each row carries the `which` the
  // regen/rebuild routes expect plus enough context to render and prefill.
  function listImages(content, featuredUrl) {
    const out = [];
    if (featuredUrl) {
      out.push({
        which: 'featured',
        url: featuredUrl,
        label: 'Featured',
        heading: null,
        alt: null,
      });
    }
    const body = typeof content === 'string' ? content : '';
    IMG_SRC_RE.lastIndex = 0;
    let m;
    let n = 0;
    while ((m = IMG_SRC_RE.exec(body)) !== null) {
      n += 1;
      // alt lives past the src match — read the rest of the tag (up to the
      // closing `>`; bounded slice when the tag is unterminated).
      const tagEnd = body.indexOf('>', m.index);
      const tagText = body.slice(
        m.index,
        tagEnd >= 0 ? tagEnd + 1 : m.index + 300
      );
      const altMatch = ALT_RE.exec(tagText);
      out.push({
        which: `inline:${n}`,
        url: m[1],
        label: `Inline #${n}`,
        heading: precedingHeading(body, m.index),
        alt: altMatch ? altMatch[1] : null,
      });
    }
    return out;
  }

  // Regen-prompt prefill for one image row: its section heading, else its
  // alt text, else the caller's fallback (post title/topic). May return ''
  // — the drawer requires a non-empty prompt before enabling Regenerate,
  // it never invents one.
  function defaultPrompt(img, fallback) {
    if (!img) return fallback || '';
    return img.heading || img.alt || fallback || '';
  }

  window.PXImages = { listImages, defaultPrompt };
})();
