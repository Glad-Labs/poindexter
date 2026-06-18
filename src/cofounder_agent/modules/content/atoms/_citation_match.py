"""Pure citation-matching core shared by the citation rail atoms.

Underscore-prefixed so ``atom_registry._walk_package`` skips it — this is a
helper module, NOT a discoverable atom. Holds the DB-free, LLM-free primitives
that power two atoms (Glad-Labs/poindexter#765):

- ``content.reconcile_citations`` (repair / scan-1): at every *attribution
  site* ("noted by X", "X points out", "according to X", "(X)"), if the named
  subject matches a source in the research corpus, wrap it in a markdown link
  to that source's URL. Prose OUTSIDE attribution sites is never touched — so a
  corpus domain like ``python.org`` can never turn every "python" in the body
  into a link.
- ``qa.unlinked_attribution`` (advisory / scan-2): flag attribution subjects
  that match NO corpus source and aren't already linked — the author-name and
  unknown-brand attributions a deterministic linker can't confidently ground.

Design rationale: the writer is *told* to cite sources inline as markdown
links but does so inconsistently, dropping the URL for some named sources while
linking others. We hold the corpus (name→URL) at draft time, so the repair is a
deterministic lookup, not a guess. Matching is tied to attribution grammar (a
verb/preposition frame) rather than "does this look like a citation?" — the
latter is the regex trap that defanged ``content_validator``'s unlinked rule.

Repair matching is intentionally STRICTER than advisory matching: repair links
only on a distinctive domain/brand match (a wrong auto-link is worse than a
missing one), while advisory also accepts a title/snippet-token match (a false
"match" there just means we DON'T flag — the safe direction).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# --- corpus model -----------------------------------------------------------


@dataclass(frozen=True)
class CorpusSource:
    """One external source from the research corpus."""

    url: str
    title: str
    text: str  # title + snippet, lowercased mining surface for handle derivation


@dataclass(frozen=True)
class Attribution:
    """A detected attribution and its named subject span in the content."""

    subject: str
    start: int
    end: int
    already_linked: bool


# --- corpus parsing ---------------------------------------------------------

# [title](http-url) markdown links — captures title + url, and the trailing
# snippet text the research formatter appends after "): ".
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
# Bare http(s) URL not preceded by ']( ' or '<' (i.e. not already a md/autolink).
_BARE_URL_RE = re.compile(r"(?<![\]\(<\"'])https?://[^\s)\]>\"']+")


def parse_corpus(research_context: str | None) -> list[CorpusSource]:
    """Extract external sources from a ``research_context`` string.

    The research formatters (``research_service.build_context`` /
    ``web_research.format_for_prompt``) render every source as a
    ``- [title](url): snippet`` bullet, so the corpus is recoverable from the
    string the writer was handed — no new plumbing through the writer needed.

    Internal ``/posts/...`` links are skipped (no scheme/host → not an external
    citation and nothing to derive a handle from). Deduped by URL,
    first-seen order preserved.
    """
    if not research_context:
        return []

    by_url: dict[str, CorpusSource] = {}

    for m in _MD_LINK_RE.finditer(research_context):
        title = m.group(1).strip()
        url = m.group(2).strip().rstrip(".,;:!?)")
        if url in by_url:
            continue
        # Grab the snippet that follows "): " up to end-of-line, if any.
        tail = research_context[m.end(): m.end() + 200]
        snippet = ""
        if tail.startswith(":"):
            snippet = tail[1:].split("\n", 1)[0].strip()
        by_url[url] = CorpusSource(
            url=url, title=title, text=f"{title} {snippet}".lower(),
        )

    for m in _BARE_URL_RE.finditer(research_context):
        url = m.group(0).rstrip(".,;:!?)")
        if url in by_url:
            continue
        by_url[url] = CorpusSource(url=url, title="", text="")

    return list(by_url.values())


# --- handle derivation + subject matching -----------------------------------

_STOPWORD_TOKENS = {
    "the", "and", "for", "with", "your", "this", "that", "how", "why",
    "what", "from", "into", "blog", "post", "docs", "guide", "article",
    "home", "index", "html", "www", "com", "org", "net",
}


def _registrable_and_sld(url: str) -> tuple[str, str] | None:
    """Return ``(registrable_domain, sld)`` for a URL, or None.

    ``https://www.getmaxim.ai/blog`` → ``("getmaxim.ai", "getmaxim")``.
    ``https://dev.to/x`` → ``("dev.to", "dev")``. Best-effort: multi-label
    public suffixes (``.co.uk``) collapse to the last two labels, which is
    imprecise but only affects an uncommon long tail.
    """
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001 — malformed URL
        return None
    if not netloc:
        return None
    netloc = netloc.split("@")[-1].split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    parts = [p for p in netloc.split(".") if p]
    if len(parts) < 2:
        return None
    return ".".join(parts[-2:]), parts[-2]


def _domain_handles(src: CorpusSource) -> set[str]:
    """Distinctive domain handles — used for high-precision REPAIR matching."""
    rs = _registrable_and_sld(src.url)
    if rs is None:
        return set()
    registrable, sld = rs
    handles = {registrable}
    if len(sld) >= 3 and sld not in _STOPWORD_TOKENS:
        handles.add(sld)
    return handles


def _title_text_tokens(src: CorpusSource) -> set[str]:
    """Significant title/snippet tokens — used only for ADVISORY matching."""
    return {
        t for t in re.findall(r"[a-z][a-z0-9]{4,}", src.text)
        if t not in _STOPWORD_TOKENS
    }


def _subject_tokens(subject: str) -> list[str]:
    """Lowercased, meaningful tokens of a subject — drops bare initials
    (``M.``) and short connective words so author names reduce to surnames."""
    out: list[str] = []
    for raw in subject.split():
        tok = raw.strip().lower().rstrip(".,;:")
        core = tok.replace(".", "")
        if len(core) <= 2:  # initials, "ai", "of", ...
            continue
        if tok in _STOPWORD_TOKENS:
            continue
        out.append(tok)
    return out


def _domain_match(subject: str, sources: list[CorpusSource]) -> CorpusSource | None:
    """Repair-grade match: subject (or a token) equals a corpus domain handle."""
    subj = subject.strip().lower().rstrip(".")
    toks = _subject_tokens(subject)
    for src in sources:
        handles = _domain_handles(src)
        if not handles:
            continue
        if subj in handles or any(t in handles for t in toks):
            return src
    return None


def match_subject(subject: str, sources: list[CorpusSource]) -> CorpusSource | None:
    """Advisory-grade match: domain handle OR a distinctive (len≥5) subject
    token present in a source's title/snippet text. Looser than ``_domain_match``
    on purpose — a false match here just suppresses a flag (the safe direction).
    """
    dm = _domain_match(subject, sources)
    if dm is not None:
        return dm
    toks = [t for t in _subject_tokens(subject) if len(t.replace(".", "")) >= 5]
    if not toks:
        return None
    for src in sources:
        tt = _title_text_tokens(src)
        if any(t in tt for t in toks):
            return src
    return None


# --- attribution detection --------------------------------------------------

# A subject token: must start with a real uppercase letter (case-sensitive even
# under IGNORECASE via the (?-i:...) scope), allowing internal caps (GetMaxim),
# dots (Kore.ai, M.), ampersands and hyphens.
_TOKEN = r"[A-Z][A-Za-z0-9.&'’-]*"
_SUBJECT_CS = rf"(?-i:{_TOKEN}(?:\s+{_TOKEN}){{0,3}})"

_PREP_VERBS = (
    r"noted|observed|argued|explained|shown|highlighted|reported|described|"
    r"demonstrated|discussed|mentioned|written|documented|outlined|detailed|stated"
)
_SUBJECT_VERBS = (
    r"points\s+out|pointed\s+out|notes|noted|argues|argued|explains|explained|"
    r"writes|wrote|reports|reported|observes|observed|states|stated|highlights|"
    r"highlighted|emphasi[sz]es|emphasi[sz]ed|warns|warned|stresses|stressed|"
    r"suggests|suggested"
)

_PREP_RE = re.compile(
    rf"(?:as\s+)?(?:{_PREP_VERBS})\s+(?:by|in)\s+({_SUBJECT_CS})", re.IGNORECASE,
)
_SUBJ_FIRST_RE = re.compile(
    rf"\b({_SUBJECT_CS})\s+(?:{_SUBJECT_VERBS})\b", re.IGNORECASE,
)
_ACCORDING_RE = re.compile(rf"according\s+to\s+({_SUBJECT_CS})", re.IGNORECASE)
_PAREN_RE = re.compile(rf"\(\s*({_SUBJECT_CS})\s*\)")

_MD_LINK_TEXT_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

# Subjects that are rhetoric / first-person / generic, not named external
# sources. Compared against the lowercased first subject token.
_SUBJECT_STOPWORDS = {
    "this", "that", "these", "those", "it", "they", "we", "our", "he", "she",
    "here", "there", "the", "a", "an", "i", "you", "research", "studies",
    "study", "data", "experts", "analysts", "sources", "many", "some", "most",
    "one", "reportedly", "glad",
}


def _markdown_link_text_spans(content: str) -> list[tuple[int, int]]:
    return [m.span(1) for m in _MD_LINK_TEXT_RE.finditer(content)]


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(not (end <= s or start >= e) for s, e in spans)


def _looks_like_source_name(subject: str) -> bool:
    """Gate parenthetical attributions: only treat ``(X)`` as a citation when X
    is brandish — multi-word, internal-caps, dotted, or an all-caps acronym —
    so ``(Recommended)`` / ``(see below)`` aren't mistaken for sources."""
    toks = subject.split()
    if len(toks) > 1:
        return True
    core = toks[0].rstrip(".")
    if any(c.isupper() for c in core[1:]):
        return True
    if "." in toks[0]:
        return True
    if core.isupper() and len(core) >= 2:
        return True
    return False


def find_attributions(content: str | None) -> list[Attribution]:
    """Detect attribution-shaped phrases and their named subject spans.

    Surfaces subjects from four frames (passive "noted by X", subject-first
    "X points out", "according to X", and brandish "(X)"). Filters rhetoric /
    first-person subjects. Each subject is flagged ``already_linked`` when its
    span overlaps a markdown link's text — those are correctly-cited and not a
    problem. Deduped by subject start offset, returned in document order.
    """
    if not content:
        return []
    link_spans = _markdown_link_text_spans(content)
    seen: set[int] = set()
    results: list[Attribution] = []
    for rx in (_PREP_RE, _SUBJ_FIRST_RE, _ACCORDING_RE, _PAREN_RE):
        for m in rx.finditer(content):
            subject = m.group(1).strip()
            if not subject:
                continue
            first_tok = subject.split()[0].lower().rstrip(".")
            if first_tok in _SUBJECT_STOPWORDS:
                continue
            if rx is _PAREN_RE and not _looks_like_source_name(subject):
                continue
            start, end = m.span(1)
            if start in seen:
                continue
            seen.add(start)
            results.append(Attribution(
                subject=subject,
                start=start,
                end=end,
                already_linked=_overlaps(start, end, link_spans),
            ))
    results.sort(key=lambda a: a.start)
    return results


# --- the two public operations ----------------------------------------------


def link_matched_attributions(
    content: str | None, sources: list[CorpusSource],
) -> tuple[str, list[dict]]:
    """REPAIR (scan-1): wrap each unlinked attribution subject that matches a
    corpus source (by domain handle) in a markdown link to that source's URL.

    Returns ``(new_content, linked)`` where ``linked`` is a list of
    ``{"subject", "url"}`` in document order. Idempotent — already-linked
    subjects are skipped, so re-running is a no-op. Prose outside attribution
    sites is never modified.
    """
    if not content or not sources:
        return content or "", []
    edits: list[tuple[int, int, str, str]] = []
    for a in find_attributions(content):
        if a.already_linked:
            continue
        src = _domain_match(a.subject, sources)
        if src is None:
            continue
        edits.append((a.start, a.end, a.subject, src.url))
    if not edits:
        return content, []
    new = content
    for start, end, subject, url in sorted(edits, key=lambda x: x[0], reverse=True):
        new = f"{new[:start]}[{subject}]({url}){new[end:]}"
    # ``edits`` is already in document order (find_attributions sorts by offset).
    linked = [{"subject": subject, "url": url} for _s, _e, subject, url in edits]
    return new, linked


def find_unmatched_attributions(
    content: str | None, sources: list[CorpusSource],
) -> list[str]:
    """ADVISORY (scan-2): subjects of unlinked attributions that match NO corpus
    source — the author names / unknown brands a deterministic linker can't
    ground. Returns ``[]`` when there's no corpus (can't tell real from
    fabricated without one — that's the deferred LLM pass's job).
    """
    if not content or not sources:
        return []
    out: list[str] = []
    for a in find_attributions(content):
        if a.already_linked:
            continue
        if match_subject(a.subject, sources) is not None:
            continue
        out.append(a.subject)
    return out


# --- re-point already-linked fabricated citations (repair, scan-3) ----------

# Multi-tenant platforms: the registrable domain is a HOST for many distinct
# sources, so "same domain, different path" means DIFFERENT CONTENT (a different
# repo / paper / article / video), NOT the same source at a wrong path. The
# writer naming the platform itself ("arXiv", "GitHub") matches the platform's
# domain handle, so without this denylist a fabricated ``arxiv.org/abs/<fake>``
# would be re-pointed to whatever arxiv source the corpus holds — silently
# citing the wrong paper. Re-pointing is high-precision ONLY on single-brand
# domains; these are excluded by construction. Registrable (2-label) forms,
# matched against ``_registrable_and_sld(url)[0]``.
DEFAULT_MULTITENANT_HOSTS: frozenset[str] = frozenset({
    "github.com", "gitlab.com", "bitbucket.org",
    "huggingface.co", "kaggle.com",
    "arxiv.org", "ssrn.com", "researchgate.net",
    "dev.to", "medium.com", "substack.com", "hashnode.dev",
    "blogspot.com", "wordpress.com", "tumblr.com",
    "wikipedia.org", "fandom.com",
    "stackoverflow.com", "stackexchange.com", "superuser.com", "serverfault.com",
    "reddit.com", "ycombinator.com", "quora.com",
    "youtube.com", "youtu.be", "vimeo.com",
    "pypi.org", "npmjs.com", "crates.io", "rubygems.org", "nuget.org", "packagist.org",
})


def repoint_fabricated_citations(
    content: str | None,
    sources: list[CorpusSource],
    multi_tenant_hosts: frozenset[str] | set[str] | None = None,
) -> tuple[str, list[dict]]:
    """REPAIR (scan-3): re-point an already-linked citation whose URL is a
    writer-invented path on a single-brand domain to the corpus source's real
    URL on that same domain.

    The writer sometimes wraps a brand in a markdown link to that brand's own
    domain but fabricates the path — a 404 the host-only ``scrub_fabricated_links``
    keeps (the host is trusted) and that ``qa.citations`` then flags dead. When
    the corpus holds the real URL for that brand on that exact domain, swapping
    the href is high-precision.

    Fires for a markdown link ``[text](url)`` only when ALL hold:

    1. ``url``'s registrable domain is NOT a multi-tenant platform (denylist) —
       there a different path is different content, so re-pointing would
       mis-cite. Defaults to :data:`DEFAULT_MULTITENANT_HOSTS`.
    2. exactly ONE corpus source sits on ``url``'s registrable domain (an
       unambiguous re-point target).
    3. ``text`` names that brand via the repair-grade ``_domain_match`` handle
       check (the same precision bar as the unlinked-attribution repair).
    4. ``url`` is not already that source's URL (idempotent; also subsumes
       "the writer used the real research URL" — never overrides a corpus URL).

    Returns ``(new_content, repointed)`` where ``repointed`` is a list of
    ``{"text", "old", "new"}`` in document order. Idempotent. Never re-points
    across domains, to an ambiguous target, or on a multi-tenant host.
    """
    if not content or not sources:
        return content or "", []

    denylist = (
        multi_tenant_hosts if multi_tenant_hosts is not None
        else DEFAULT_MULTITENANT_HOSTS
    )

    # registrable domain -> sole corpus source on it (None marks 2+ → ambiguous).
    by_domain: dict[str, CorpusSource | None] = {}
    for src in sources:
        rs = _registrable_and_sld(src.url)
        if rs is None:
            continue
        reg = rs[0]
        by_domain[reg] = None if reg in by_domain else src

    edits: list[tuple[int, int, str, str, str]] = []
    for m in _MD_LINK_RE.finditer(content):
        text = m.group(1).strip()
        url = m.group(2).strip().rstrip(".,;:!?)")
        rs = _registrable_and_sld(url)
        if rs is None:
            continue
        reg = rs[0]
        if reg in denylist:
            continue
        src = by_domain.get(reg)
        if src is None:  # no corpus source on this domain, or ambiguous (2+)
            continue
        if url == src.url:  # already the real URL — nothing to fix
            continue
        if _domain_match(text, [src]) is None:  # link text must name the brand
            continue
        url_start, url_end = m.span(2)
        edits.append((url_start, url_end, text, url, src.url))

    if not edits:
        return content, []

    new = content
    for url_start, url_end, _text, _old, new_url in sorted(
        edits, key=lambda x: x[0], reverse=True,
    ):
        new = f"{new[:url_start]}{new_url}{new[url_end:]}"
    repointed = [{"text": t, "old": o, "new": n} for _s, _e, t, o, n in edits]
    return new, repointed


__all__ = [
    "Attribution",
    "CorpusSource",
    "DEFAULT_MULTITENANT_HOSTS",
    "find_attributions",
    "find_unmatched_attributions",
    "link_matched_attributions",
    "match_subject",
    "parse_corpus",
    "repoint_fabricated_citations",
]
