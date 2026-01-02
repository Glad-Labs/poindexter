# ARCHIVED - Use src/cofounder_agent/services/content_quality_service.py instead
# This file is deprecated and kept for reference only

"""
LEGACY - ARCHIVED (300+ lines)

Original QAAgent from src/agents/content_agent/agents/qa_agent.py
Now consolidated into unified ContentQualityService

This file provided:
- Binary approval/rejection of content
- LLM-based quality feedback
- Integration with iterative refinement
- JSON response parsing

Now fully integrated into ContentQualityService with:
- 7-criteria scoring framework
- Pattern-based & LLM-based evaluation modes
- Hybrid scoring for robustness
- PostgreSQL persistence
- Automatic suggestions

For reference only. Do not use for new features.
The ContentQualityService.to_approval_tuple() method provides backward compatibility.
"""

# [FULL FILE ARCHIVED - See src/cofounder_agent/services/content_quality_service.py for current implementation]
