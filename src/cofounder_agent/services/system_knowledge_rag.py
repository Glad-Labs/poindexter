"""
System Knowledge RAG Service

Provides semantic search and retrieval of system knowledge about Glad Labs.
Uses similarity scoring to find relevant knowledge base sections.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class KnowledgeResult:
    """Result from knowledge base retrieval"""

    content: str
    confidence: float  # 0-1 score
    source_section: str
    is_structured_answer: bool = False


class SystemKnowledgeRAG:
    """
    Retrieval-Augmented Generation for system knowledge.
    Loads knowledge base and provides semantic search.
    """

    def __init__(self):
        """Initialize knowledge base from markdown file"""
        self.knowledge_base = self._load_knowledge_base()
        self.sections = self._parse_sections()
        self.is_initialized = bool(self.knowledge_base)

    def _load_knowledge_base(self) -> str:
        """Load system knowledge markdown file"""
        kb_path = Path(__file__).parent.parent / "data" / "system_knowledge.md"

        if not kb_path.exists():
            return ""

        try:
            with open(kb_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def _parse_sections(self) -> dict:
        """Parse knowledge base into sections"""
        sections = {}

        if not self.knowledge_base:
            return sections

        # Extract all H2 sections (## Title)
        pattern = r"## (.+?)\n(.*?)(?=## |\Z)"
        matches = re.finditer(pattern, self.knowledge_base, re.DOTALL)

        for match in matches:
            title = match.group(1).strip()
            content = match.group(2).strip()
            sections[title] = content

        return sections

    def retrieve(self, query: str, confidence_threshold: float = 0.6) -> Optional[KnowledgeResult]:
        """
        Retrieve knowledge relevant to query.

        Args:
            query: User question or search query
            confidence_threshold: Minimum confidence score (0-1)

        Returns:
            KnowledgeResult if found, None otherwise
        """
        if not self.is_initialized:
            return None

        # Check for direct question patterns
        result = self._check_structured_questions(query)
        if result:
            return result

        # Semantic search through sections
        best_match = self._semantic_search(query)
        if best_match and best_match.confidence >= confidence_threshold:
            return best_match

        # Return knowledge base intro with lower confidence
        kb_intro = self.knowledge_base.split("\n\n")[0:5]
        intro_text = "\n\n".join(kb_intro)
        return KnowledgeResult(
            content=intro_text,
            confidence=0.5,
            source_section="Introduction",
            is_structured_answer=False,
        )

    def _check_structured_questions(self, query: str) -> Optional[KnowledgeResult]:
        """
        Check for common structured questions and return direct answers.
        These are high-confidence answers.
        """
        query_lower = query.lower()

        # Programming languages
        if any(
            kw in query_lower
            for kw in ["programming language", "built with", "tech stack", "technology"]
        ):
            return KnowledgeResult(
                content="Glad Labs is built with Python (backend), JavaScript and TypeScript (frontend), HTML/CSS, and React 18. Backend uses FastAPI framework on Python 3.12+, frontend uses Next.js 15 and React 18 with TailwindCSS, and admin UI uses Material-UI.",
                confidence=0.95,
                source_section="Technology Stack",
                is_structured_answer=True,
            )

        # Agent types
        if any(
            kw in query_lower for kw in ["agent type", "agent", "agents available", "which agent"]
        ):
            return KnowledgeResult(
                content="Glad Labs has 5 specialized agents: (1) Content Agent for blog/article generation, (2) Financial Agent for cost tracking and ROI analysis, (3) Market Insight Agent for trend analysis and competitive intelligence, (4) Compliance Agent for legal/risk review, and (5) Orchestrator Agent (Co-Founder) for master workflow coordination.",
                confidence=0.95,
                source_section="Specialized Agent System",
                is_structured_answer=True,
            )

        # LLM providers
        if any(
            kw in query_lower
            for kw in ["provider", "llm", "model", "supported provider", "how many provider"]
        ):
            return KnowledgeResult(
                content="Glad Labs supports 5 LLM providers: (1) Ollama (local, free), (2) Anthropic Claude (requires API key), (3) OpenAI (requires API key), (4) Google Gemini (requires API key), and (5) HuggingFace (requires API key). The system uses intelligent routing with fallback from Ollama → Anthropic → OpenAI → Google.",
                confidence=0.95,
                source_section="LLM Provider Integration",
                is_structured_answer=True,
            )

        # Database
        if any(kw in query_lower for kw in ["database", "data storage", "postgres"]):
            return KnowledgeResult(
                content="Glad Labs uses PostgreSQL as the primary database on port 5432. It stores all persistent data including users, tasks, content, workflows, execution history, and writing samples. The system uses SQLAlchemy ORM with Alembic for migrations.",
                confidence=0.95,
                source_section="Database",
                is_structured_answer=True,
            )

        # Ports/architecture
        if any(kw in query_lower for kw in ["port", "what port", "run on", "listen", "localhost"]):
            return KnowledgeResult(
                content="Glad Labs runs on three ports: Backend (FastAPI) runs on port 8000, Public Site (Next.js) runs on port 3000, and Oversight Hub (React Admin) runs on port 3001. All services are started together with 'npm run dev'.",
                confidence=0.90,
                source_section="Architecture",
                is_structured_answer=True,
            )

        # Workflow
        if any(kw in query_lower for kw in ["workflow", "phase", "task execution", "pipeline"]):
            return KnowledgeResult(
                content="Glad Labs workflows use a phase-based system with automatic input/output mapping. Phases execute sequentially, with data flowing from one phase to the next. The system tracks data provenance and includes graceful error handling. Tasks are executed in the background with status updates via WebSocket.",
                confidence=0.90,
                source_section="Workflow System",
                is_structured_answer=True,
            )

        # Quality assessment
        if any(kw in query_lower for kw in ["quality", "assessment", "score", "evaluation"]):
            return KnowledgeResult(
                content="Glad Labs uses a 6-point quality assessment framework: (1) Tone and Voice, (2) Structure, (3) SEO, (4) Engagement, (5) Accuracy, and (6) Writing Style Consistency. Content scores range 0-100, with 75+ being excellent, 40-75 being good, and below 40 being draft quality.",
                confidence=0.90,
                source_section="Quality Assessment Framework",
                is_structured_answer=True,
            )

        return None

    def _semantic_search(self, query: str) -> Optional[KnowledgeResult]:
        """
        Perform semantic search through knowledge base sections.
        Uses Jaccard similarity and keyword matching.
        """
        if not self.sections:
            return None

        query_tokens = set(self._tokenize(query))
        best_match = None
        best_score = 0

        for section_title, section_content in self.sections.items():
            # Score based on keyword overlap
            section_tokens = set(self._tokenize(section_content))
            section_title_tokens = set(self._tokenize(section_title))

            # Jaccard similarity
            intersection = query_tokens & section_tokens
            union = query_tokens | section_tokens
            jaccard = len(intersection) / len(union) if union else 0

            # Title bonus (section title match is strong signal)
            title_score = (
                len(query_tokens & section_title_tokens) / len(query_tokens) if query_tokens else 0
            )

            total_score = (jaccard * 0.7) + (title_score * 0.3)

            if total_score > best_score:
                best_score = total_score
                best_match = (section_title, section_content)

        if best_match and best_score > 0.1:  # Minimum threshold
            title, content = best_match
            # Truncate very long sections
            truncated = self._truncate_content(content, max_sentences=10)
            return KnowledgeResult(
                content=truncated,
                confidence=min(best_score, 1.0),
                source_section=title,
                is_structured_answer=False,
            )

        return None

    def _tokenize(self, text: str) -> list:
        """Simple tokenization"""
        # Convert to lowercase and split on non-alphanumeric
        text = text.lower()
        tokens = re.findall(r"\b\w+\b", text)
        # Filter out very short tokens
        return [t for t in tokens if len(t) > 2]

    def _truncate_content(self, content: str, max_sentences: int = 10) -> str:
        """Truncate content to max sentences"""
        sentences = re.split(r"[.!?]+", content)
        sentences = [s.strip() for s in sentences if s.strip()][:max_sentences]
        return ". ".join(sentences) + "."

    def retrieve_by_keyword(self, keywords: List[str]) -> Optional[KnowledgeResult]:
        """
        Retrieve knowledge by specific keywords.
        More targeted than semantic search.
        """
        if not keywords or not self.sections:
            return None

        keyword_set = set(kw.lower() for kw in keywords)
        best_match = None
        best_score = 0

        for section_title, section_content in self.sections.items():
            section_text = (section_title + " " + section_content).lower()
            matches = sum(1 for kw in keyword_set if kw in section_text)
            score = matches / len(keyword_set) if keyword_set else 0

            if score > best_score:
                best_score = score
                best_match = (section_title, section_content)

        if best_match and best_score > 0:
            title, content = best_match
            truncated = self._truncate_content(content, max_sentences=8)
            return KnowledgeResult(
                content=truncated,
                confidence=min(best_score, 1.0),
                source_section=title,
                is_structured_answer=False,
            )

        return None

    def get_section(self, section_name: str) -> Optional[str]:
        """Get specific section by exact name"""
        return self.sections.get(section_name)

    def list_sections(self) -> List[str]:
        """List all available sections"""
        return list(self.sections.keys())

    def search_multiple(self, query: str, limit: int = 3) -> List[KnowledgeResult]:
        """
        Search for multiple matching sections.
        Returns top results by relevance.
        """
        if not self.sections:
            return []

        query_tokens = set(self._tokenize(query))
        results = []

        for section_title, section_content in self.sections.items():
            section_tokens = set(self._tokenize(section_content))
            section_title_tokens = set(self._tokenize(section_title))

            intersection = query_tokens & section_tokens
            union = query_tokens | section_tokens
            jaccard = len(intersection) / len(union) if union else 0
            title_score = (
                len(query_tokens & section_title_tokens) / len(query_tokens) if query_tokens else 0
            )

            total_score = (jaccard * 0.7) + (title_score * 0.3)

            if total_score > 0.05:
                truncated = self._truncate_content(section_content, max_sentences=5)
                results.append(
                    KnowledgeResult(
                        content=truncated,
                        confidence=min(total_score, 1.0),
                        source_section=section_title,
                        is_structured_answer=False,
                    )
                )

        # Sort by confidence descending
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results[:limit]


# Singleton instance
_system_knowledge_rag: Optional[SystemKnowledgeRAG] = None


def get_system_knowledge_rag() -> SystemKnowledgeRAG:
    """Get or create singleton instance"""
    global _system_knowledge_rag
    if _system_knowledge_rag is None:
        _system_knowledge_rag = SystemKnowledgeRAG()
    return _system_knowledge_rag
