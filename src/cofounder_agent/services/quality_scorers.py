"""
Quality Scoring Functions

Pure scoring functions extracted from UnifiedQualityService.
These are stateless heuristics that score content on various quality
dimensions (0-10 scale) using pattern-based analysis.

All functions that previously accessed self._qa_cfg() now accept a
``cfg`` dict parameter or call ``qa_cfg()`` directly.
"""

import re
from typing import Any, Dict, List

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def qa_cfg() -> dict:
    """Load all QA pipeline thresholds from DB via site_config.

    Every threshold in the QA pipeline is tunable via app_settings
    (key prefix: qa_). Returns a dict of all values with sensible defaults.
    Change any value with a simple SQL UPDATE on app_settings.
    """
    from services.site_config import site_config

    return {
        # --- Overall pipeline ---
        "pass_threshold": site_config.get_float("qa_pass_threshold", 70.0),
        "critical_floor": site_config.get_float("qa_critical_floor", 50.0),
        "artifact_penalty_per": site_config.get_float("qa_artifact_penalty_per", 5.0),
        "artifact_penalty_max": site_config.get_float("qa_artifact_penalty_max", 20.0),
        # --- Flesch-Kincaid target ---
        "fk_target_min": site_config.get_float("qa_fk_target_min", 8.0),
        "fk_target_max": site_config.get_float("qa_fk_target_max", 12.0),
        # --- Clarity ---
        "clarity_ideal_min": site_config.get_int("qa_clarity_ideal_min_wps", 15),
        "clarity_ideal_max": site_config.get_int("qa_clarity_ideal_max_wps", 20),
        "clarity_good_min": site_config.get_int("qa_clarity_good_min_wps", 10),
        "clarity_good_max": site_config.get_int("qa_clarity_good_max_wps", 25),
        "clarity_ok_min": site_config.get_int("qa_clarity_ok_min_wps", 8),
        "clarity_ok_max": site_config.get_int("qa_clarity_ok_max_wps", 30),
        # --- Accuracy ---
        "accuracy_baseline": site_config.get_float("qa_accuracy_baseline", 7.0),
        "accuracy_good_link_bonus": site_config.get_float("qa_accuracy_good_link_bonus", 0.3),
        "accuracy_good_link_max": site_config.get_float("qa_accuracy_good_link_max_bonus", 1.0),
        "accuracy_bad_link_penalty": site_config.get_float("qa_accuracy_bad_link_penalty", 0.5),
        "accuracy_bad_link_max": site_config.get_float("qa_accuracy_bad_link_max_penalty", 2.0),
        "accuracy_citation_bonus": site_config.get_float("qa_accuracy_citation_bonus", 0.3),
        "accuracy_first_person_penalty": site_config.get_float("qa_accuracy_first_person_penalty", 1.0),
        "accuracy_first_person_max": site_config.get_float("qa_accuracy_first_person_max_penalty", 3.0),
        "accuracy_meta_commentary_penalty": site_config.get_float("qa_accuracy_meta_commentary_penalty", 0.5),
        "accuracy_meta_commentary_max": site_config.get_float("qa_accuracy_meta_commentary_max_penalty", 2.0),
        # --- Completeness ---
        "completeness_word_2000": site_config.get_float("qa_completeness_word_2000_score", 6.5),
        "completeness_word_1500": site_config.get_float("qa_completeness_word_1500_score", 6.0),
        "completeness_word_1000": site_config.get_float("qa_completeness_word_1000_score", 5.0),
        "completeness_word_500": site_config.get_float("qa_completeness_word_500_score", 3.5),
        "completeness_word_min": site_config.get_float("qa_completeness_word_min_score", 2.0),
        "completeness_heading_bonus": site_config.get_float("qa_completeness_heading_bonus", 0.3),
        "completeness_heading_max": site_config.get_float("qa_completeness_heading_max_bonus", 1.5),
        "completeness_truncation_penalty": site_config.get_float("qa_completeness_truncation_penalty", 3.0),
        # --- Relevance ---
        "relevance_no_topic_default": site_config.get_float("qa_relevance_no_topic_default", 6.0),
        "relevance_high_coverage": site_config.get_float("qa_relevance_high_coverage_score", 8.5),
        "relevance_med_coverage": site_config.get_float("qa_relevance_med_coverage_score", 7.0),
        "relevance_low_coverage": site_config.get_float("qa_relevance_low_coverage_score", 5.5),
        "relevance_none_coverage": site_config.get_float("qa_relevance_none_coverage_score", 3.0),
        "relevance_stuffing_hard": site_config.get_float("qa_relevance_stuffing_hard_density", 5.0),
        "relevance_stuffing_soft": site_config.get_float("qa_relevance_stuffing_soft_density", 3.0),
        # --- SEO ---
        "seo_baseline": site_config.get_float("qa_seo_baseline", 6.0),
        # --- Engagement ---
        "engagement_baseline": site_config.get_float("qa_engagement_baseline", 6.0),
    }


# ---------------------------------------------------------------------------
# Scoring functions (all return 0-10 scale)
# ---------------------------------------------------------------------------

def score_clarity(content: str, sentence_count: int, word_count: int, cfg: dict | None = None) -> float:
    """Score clarity based on sentence structure and word count.
    Thresholds tunable via qa_clarity_* app_settings keys."""
    cfg = cfg or qa_cfg()
    if word_count == 0 or sentence_count == 0:
        return 5.0

    avg_words_per_sentence = word_count / sentence_count

    if cfg["clarity_ideal_min"] <= avg_words_per_sentence <= cfg["clarity_ideal_max"]:
        return 9.0
    if cfg["clarity_good_min"] <= avg_words_per_sentence <= cfg["clarity_good_max"]:
        return 8.0
    if cfg["clarity_ok_min"] <= avg_words_per_sentence <= cfg["clarity_ok_max"]:
        return 7.0
    return 5.0


def score_accuracy(content: str, context: dict[str, Any], cfg: dict | None = None) -> float:
    """Score accuracy based on citation patterns and factual anchors.
    Thresholds tunable via qa_accuracy_* app_settings keys."""
    cfg = cfg or qa_cfg()
    score = cfg["accuracy_baseline"]
    content_lower = content.lower()

    # External links: only count links to known reputable domains.
    # Shares the same settings key (trusted_source_domains) as the
    # content_router, so one list covers both external-link validation
    # and citation credibility (#198).
    all_links = re.findall(r"https?://([^\s\)\]\"'>]+)", content)
    from services.site_config import site_config as _sc
    _domain = _sc.get("site_domain", "")
    _override_csv = _sc.get("trusted_source_domains", "")
    _default_reputable = {
        "github.com", "arxiv.org", "docs.python.org", "docs.rs",
        "developer.mozilla.org", "stackoverflow.com", "wikipedia.org",
        "news.ycombinator.com", "devto.dev", "dev.to", "blog.rust-lang.org",
        "go.dev", "kubernetes.io", "docker.com", "vercel.com", "nextjs.org",
        "react.dev", "pytorch.org", "huggingface.co", "openai.com",
    }
    if _override_csv:
        reputable_domains = {
            d.strip().lower() for d in _override_csv.split(",") if d.strip()
        }
    else:
        reputable_domains = set(_default_reputable)
    if _domain:
        reputable_domains.add(_domain)
        reputable_domains.add(f"www.{_domain}")
    good_links = sum(1 for link in all_links if any(d in link for d in reputable_domains))
    bad_links = len(all_links) - good_links
    score += min(good_links * cfg["accuracy_good_link_bonus"], cfg["accuracy_good_link_max"])
    score -= min(bad_links * cfg["accuracy_bad_link_penalty"], cfg["accuracy_bad_link_max"])

    # Citation/reference patterns: [1], (Smith 2023), Source:, References:
    citation_patterns = [
        r"\[\d+\]",  # [1], [12]
        r"\(\w[^)]{1,40}\d{4}\)",  # (Author 2023)
        r"(?:source|reference|cited|per|via):",
        r"according to\b",
        r"research (?:shows?|suggests?|finds?|indicates?)\b",
        r"studies? (?:show|suggest|find|indicate)\b",
        r"published (?:in|by)\b",
    ]
    for pat in citation_patterns:
        if re.search(pat, content_lower):
            score += cfg["accuracy_citation_bonus"]

    # Named quotes in proper context (not decorative use of quotation marks)
    named_quote = re.search(r'"[^"]{10,}"[,\s]+(?:said|wrote|noted|according)', content)
    if named_quote:
        score += 0.5

    # Voice violation: penalize first-person claims about building/creating things
    first_person_claims = len(re.findall(
        r"\b(?:I|we)\s+(?:built|created|developed|designed|made|launched|shipped|released|wrote)\b",
        content, re.IGNORECASE,
    ))
    if first_person_claims > 0:
        score -= min(
            first_person_claims * cfg["accuracy_first_person_penalty"],
            cfg["accuracy_first_person_max"],
        )

    # Meta-commentary penalty
    meta_commentary = len(re.findall(
        r"\b(?:this\s+(?:post|article|blog|piece)\s+(?:explores?|examines?|discusses?|looks\s+at|covers?|delves?))"
        r"|\b(?:in\s+this\s+(?:post|article|blog|piece))"
        r"|\b(?:(?:we.ll|let.s|we\s+will)\s+(?:explore|discuss|examine|look\s+at|dive\s+into))",
        content, re.IGNORECASE,
    ))
    if meta_commentary > 0:
        score -= min(
            meta_commentary * cfg["accuracy_meta_commentary_penalty"],
            cfg["accuracy_meta_commentary_max"],
        )

    return min(max(score, 0.0), 10.0)


def score_completeness(content: str, context: dict[str, Any], cfg: dict | None = None) -> float:
    """Score completeness based on depth signals beyond raw word count.
    Thresholds tunable via qa_completeness_* app_settings keys."""
    cfg = cfg or qa_cfg()
    word_count = len(content.split())
    score = 0.0

    # Word-count baseline
    if word_count >= 2000:
        score += cfg["completeness_word_2000"]
    elif word_count >= 1500:
        score += cfg["completeness_word_1500"]
    elif word_count >= 1000:
        score += cfg["completeness_word_1000"]
    elif word_count >= 500:
        score += cfg["completeness_word_500"]
    else:
        score += cfg["completeness_word_min"]

    # Structural depth signals
    heading_count = len(re.findall(r"^#{1,3}\s", content, re.MULTILINE))
    score += min(heading_count * cfg["completeness_heading_bonus"], cfg["completeness_heading_max"])

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if len(paragraphs) >= 5:
        score += 0.5

    # Intro/conclusion present (first and last paragraphs are non-trivial)
    if paragraphs and len(paragraphs[0].split()) >= 30:
        score += 0.5
    if len(paragraphs) > 1 and len(paragraphs[-1].split()) >= 20:
        score += 0.5

    # Contains lists (signals structured coverage)
    if re.search(r"^[-*]\s", content, re.MULTILINE):
        score += 0.5

    # Truncation penalty -- content cut off mid-sentence by LLM token limit
    if detect_truncation(content):
        score = max(score - cfg["completeness_truncation_penalty"], 0.0)

    return min(score, 10.0)


def score_relevance(content: str, context: dict[str, Any], cfg: dict | None = None) -> float:
    """Score relevance using topic-word family matching to resist keyword stuffing.
    Thresholds tunable via qa_relevance_* app_settings keys."""
    cfg = cfg or qa_cfg()
    topic = context.get("topic", "") or context.get("primary_keyword", "")
    if not topic:
        return cfg["relevance_no_topic_default"]

    content_lower = content.lower()
    topic_words = [w.lower() for w in re.findall(r"\b\w{4,}\b", topic)]
    word_count = len(content.split())

    if not topic_words or word_count == 0:
        return cfg["relevance_no_topic_default"]

    matched_words = sum(1 for w in topic_words if w in content_lower)
    coverage = matched_words / len(topic_words)

    exact_count = content_lower.count(topic.lower())
    density = exact_count / (word_count / 100)

    if coverage >= 0.8:
        base = cfg["relevance_high_coverage"]
    elif coverage >= 0.5:
        base = cfg["relevance_med_coverage"]
    elif coverage >= 0.25:
        base = cfg["relevance_low_coverage"]
    else:
        base = cfg["relevance_none_coverage"]

    if density > cfg["relevance_stuffing_hard"]:
        base = min(base, 5.5)
    elif density > cfg["relevance_stuffing_soft"]:
        base = min(base, 7.0)

    return min(base, 10.0)


def score_seo(content: str, context: dict[str, Any], cfg: dict | None = None) -> float:
    """Score SEO quality. Baseline tunable via qa_seo_baseline.

    Awards points for:
    - Markdown headers (+1.0)
    - Paragraph breaks (+1.0)
    - Topic in the opening line (+1.0)
    - Primary keywords present anywhere in the content (+1.5)
    - Primary keywords missing (-1.0, dragging an otherwise-fine post
      below the passing threshold)
    """
    cfg = cfg or qa_cfg()
    score = cfg["seo_baseline"]

    # Check for headers
    if "#" in content or re.search(r"#+\s", content):
        score += 1.0

    # Check for internal structure
    if "\n\n" in content:
        score += 1.0

    # Check keyword in beginning
    topic = context.get("topic", "")
    if topic and content.lower().startswith(topic.lower()):
        score += 1.0

    # Primary keyword presence. Previously the pipeline computed
    # check_keywords() and threw the result away, so a post could
    # completely ignore the requested keywords and still get the
    # full SEO score.
    context_has_keywords = bool(context.get("keywords"))
    if context_has_keywords:
        if check_keywords(content, context):
            score += 1.5
        else:
            score -= 1.0

    return max(0.0, min(score, 10.0))


def score_readability(content: str) -> float:
    """Score readability using Flesch Reading Ease with technical content adjustment.

    The raw Flesch formula heavily penalizes polysyllabic technical terms
    (PostgreSQL, Kubernetes, infrastructure) causing valid technical writing
    to score 20-40. We apply a floor of 5.5/10 for technical content and
    compress the scale so technical writing lands in 5.5-8.5 range.
    """
    words = content.split()
    sentences = len(re.split(r"[.!?]+", content))
    syllables = sum(count_syllables(word) for word in words)

    if len(words) == 0 or sentences == 0:
        return 7.0

    # Flesch Reading Ease approximation (0-100 scale)
    flesch = 206.835 - 1.015 * (len(words) / sentences) - 84.6 * (syllables / len(words))

    # Technical content floor: Flesch scores below 30 are normal for
    # technical writing (PostgreSQL, Kubernetes, microservices). Don't let
    # readability tank the overall score for valid technical prose.
    # Scale: Flesch 0->7.0, 30->7.5, 60->8.0, 100->9.0
    if flesch >= 60:
        return min(10.0, 8.0 + (flesch - 60) * 0.025)  # 60->8.0, 100->9.0
    elif flesch >= 30:
        return 7.5 + (flesch - 30) * 0.017  # 30->7.5, 60->8.0
    else:
        return max(7.0, 7.0 + flesch * 0.017)  # 0->7.0, 30->7.5


def score_engagement(content: str, cfg: dict | None = None) -> float:
    """Score engagement based on structure and style. Baseline tunable via qa_engagement_baseline."""
    cfg = cfg or qa_cfg()
    score = cfg["engagement_baseline"]

    # Bullet points / lists
    if "- " in content or "* " in content:
        score += 1.0

    # Questions (hooks the reader)
    question_count = content.count("?")
    if question_count >= 3:
        score += 1.0
    elif question_count >= 1:
        score += 0.5

    # Varied paragraph length (good pacing)
    paragraphs = [p for p in content.split("\n\n") if p.strip()]
    if len(paragraphs) >= 4:
        score += 0.5
    if len(set(len(p.split()) // 10 for p in paragraphs)) > 2:
        score += 0.5

    # Code blocks (technical engagement signal)
    if "```" in content:
        score += 0.5

    # Bold/emphasis (highlights key points)
    if "**" in content or "__" in content:
        score += 0.5

    return min(score, 10.0)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def check_keywords(content: str, context: dict[str, Any]) -> bool:
    """Check if keywords are present in content."""
    context = context or {}
    keywords = context.get("keywords")
    if keywords is None:
        keywords = []
    elif isinstance(keywords, str):
        keywords = [keywords]
    elif not isinstance(keywords, list):
        keywords = [str(keywords)]

    keywords = [kw for kw in keywords if isinstance(kw, str) and kw.strip()]
    content_lower = content.lower()

    return any(kw.lower() in content_lower for kw in keywords)


def count_syllables(word: str) -> int:
    """Estimate syllable count."""
    word = word.lower()
    syllable_count = 0
    vowels = "aeiou"
    previous_was_vowel = False

    for char in word:
        is_vowel = char in vowels
        if is_vowel and not previous_was_vowel:
            syllable_count += 1
        previous_was_vowel = is_vowel

    return max(1, syllable_count)


def flesch_kincaid_grade_level(text: str) -> float:
    """Compute the Flesch-Kincaid Grade Level for *text*.

    Formula:
        0.39 * (total_words / total_sentences)
        + 11.8 * (total_syllables / total_words)
        - 15.59

    Syllable counting uses a simple vowel-group heuristic: each
    consecutive run of vowels (a, e, i, o, u) in a word counts as
    one syllable, with a minimum of 1 syllable per word.

    Returns the grade level as a float.  Lower values indicate easier
    readability (target: 8-12 for general audience content).
    """
    if not text or not text.strip():
        return 0.0

    # Strip HTML/markdown for cleaner analysis
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"#{1,6}\s", "", clean)

    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", clean)
    total_words = len(words)
    if total_words == 0:
        return 0.0

    # Sentence splitting: split on . ! ? (ignore abbreviations as noise)
    sentences = [s for s in re.split(r"[.!?]+", clean) if s.strip()]
    total_sentences = max(len(sentences), 1)

    # Count syllables using vowel-group heuristic
    def _count_syllables(word: str) -> int:
        word = word.lower()
        count = 0
        prev_vowel = False
        for ch in word:
            is_vowel = ch in "aeiou"
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel
        return max(count, 1)

    total_syllables = sum(_count_syllables(w) for w in words)

    grade = (
        0.39 * (total_words / total_sentences)
        + 11.8 * (total_syllables / total_words)
        - 15.59
    )
    return round(grade, 2)


def detect_truncation(content: str) -> bool:
    """Detect if content was truncated by LLM token limits.

    Checks whether the content ends mid-sentence, which is a strong signal
    that the LLM hit its output token limit before completing the article.
    """
    if not content or len(content.strip()) < 100:
        return False

    # Strip HTML tags for analysis
    text = re.sub(r"<[^>]+>", "", content).strip()
    if not text:
        return False

    # Get the last non-empty line
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return False

    last_line = lines[-1]

    # Content ending with terminal punctuation is complete
    if last_line[-1] in ".!?)\"'":
        return False

    # Content ending with a URL or link is likely a references section (OK)
    if re.search(r"https?://\S+$", last_line):
        return False

    # Content ending with a markdown/HTML heading is truncated
    if re.match(r"^#{1,6}\s", last_line):
        return True

    # If the last line is very short and looks like a fragment, it's truncated
    if not last_line[-1] in ".!?)\"':*" and len(last_line) > 20:
        logger.warning(
            f"[TRUNCATION] Content appears truncated -- last line: ...{last_line[-80:]}"
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Feedback generation
# ---------------------------------------------------------------------------

def generate_feedback(dimensions: Any, context: dict[str, Any]) -> str:
    """Generate human-readable feedback.

    ``dimensions`` is expected to have an ``.average()`` method (e.g.
    ``QualityDimensions``).
    """
    overall = dimensions.average()

    if overall >= 85:
        return "Excellent content quality - publication ready"
    if overall >= 75:
        return "Good quality - minor improvements recommended"
    if overall >= 70:
        return "Acceptable quality - some improvements suggested"
    if overall >= 60:
        return "Fair quality - significant improvements needed"
    return "Poor quality - major revisions required"


def generate_suggestions(dimensions: Any) -> list[str]:
    """Generate improvement suggestions based on weak dimensions.

    ``dimensions`` is expected to have dimension attributes on a 0-100 scale
    (e.g. ``QualityDimensions``).
    """
    suggestions = []
    threshold = 70  # 0-100 scale

    if dimensions.clarity < threshold:
        suggestions.append("Simplify sentence structure and use shorter sentences")
    if dimensions.accuracy < threshold:
        suggestions.append("Fact-check claims and add citations where appropriate")
    if dimensions.completeness < threshold:
        suggestions.append("Add more detail and cover the topic more thoroughly")
    if dimensions.relevance < threshold:
        suggestions.append("Keep content focused on the main topic")
    if dimensions.seo_quality < threshold:
        suggestions.append("Improve SEO with better headers, keywords, and structure")
    if dimensions.readability < threshold:
        suggestions.append("Improve grammar and readability")
    if dimensions.engagement < threshold:
        suggestions.append("Add engaging elements like questions, lists, or examples")

    return suggestions or ["Content meets quality standards"]
