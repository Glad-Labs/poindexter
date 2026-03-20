"""
Phase Mapper - Automatic input/output mapping between workflow phases

This module handles connecting the output of one phase to the input of the next phase.
It uses semantic matching to find appropriate field connections.
"""

from services.logger_config import get_logger
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from services.phase_registry import PhaseDefinition, PhaseRegistry

logger = get_logger(__name__)
class PhaseMappingError(Exception):
    """Raised when phase mapping fails"""



class PhaseMapper:
    """
    Automatically maps outputs from one phase to inputs of the next phase.

    Matching strategy (in order of priority):
    1. Exact key match (e.g., output["content"] -> input["content"])
    2. Semantic similarity (labels/descriptions match)
    3. Largest output (by content size) goes to first unfilled input
    4. Manual override/user mapping
    """

    def __init__(self, registry: Optional[PhaseRegistry] = None):
        self.registry = registry or PhaseRegistry.get_instance()

    def map_phases(
        self,
        source_phase_name: str,
        target_phase_name: str,
        user_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Generate mapping from source phase outputs to target phase inputs.

        Args:
            source_phase_name: Name of previous phase (output provider)
            target_phase_name: Name of next phase (input consumer)
            user_overrides: User-defined mapping overrides {target_input_key: source_output_key}

        Returns:
            Mapping dict: {target_input_key: source_output_key}
            Example: {"content": "findings", "topic": "research_topic"}

        Raises:
            PhaseMappingError: If phases don't exist or mapping is impossible
        """

        # Get phase definitions
        source_def = self.registry.get_phase(source_phase_name)
        target_def = self.registry.get_phase(target_phase_name)

        if not source_def:
            raise PhaseMappingError(f"Source phase '{source_phase_name}' not found in registry")
        if not target_def:
            raise PhaseMappingError(f"Target phase '{target_phase_name}' not found in registry")

        mapping = {}

        # Start with user overrides (highest priority)
        if user_overrides:
            mapping.update(user_overrides)

        # Apply automatic matching for unmapped required inputs
        for target_key, target_input in target_def.input_schema.items():
            if target_key in mapping:
                # Already mapped by user
                continue

            # Try to find a source output field
            source_key = self._find_best_match(target_key, target_input, source_def.output_schema)

            if source_key:
                mapping[target_key] = source_key
                logger.debug(
                    f"Auto-mapped {target_phase_name}.{target_key} "
                    f"<- {source_phase_name}.{source_key}"
                )

        return mapping

    def _find_best_match(
        self, target_key: str, target_input: Any, source_outputs: Dict[str, Any]
    ) -> Optional[str]:
        """
        Find the best matching output field for a given input field.

        Uses semantic matching on field names, labels, and descriptions.
        """

        if not source_outputs:
            return None

        # Strategy 1: Exact key match
        if target_key in source_outputs:
            logger.debug(f"Found exact key match: {target_key}")
            return target_key

        # Strategy 2: Semantic similarity matching
        candidates = self._rank_by_similarity(target_key, target_input, source_outputs)

        if candidates:
            best_match = candidates[0]  # Highest similarity score
            logger.debug(
                f"Found semantic match: {target_key} -> {best_match[0]} "
                f"(similarity: {best_match[1]:.2f})"
            )
            return best_match[0]

        return None

    def _rank_by_similarity(
        self, target_key: str, target_input: Any, source_outputs: Dict[str, Any]
    ) -> List[Tuple[str, float]]:
        """
        Rank source output fields by semantic similarity to target input.

        Returns list of (source_key, similarity_score) tuples, sorted by score descending.
        """

        scores = []

        target_label = getattr(target_input, "label", target_key).lower()
        target_desc = getattr(target_input, "description", "").lower()

        for source_key, source_output in source_outputs.items():
            source_label = getattr(source_output, "label", source_key).lower()
            source_desc = getattr(source_output, "description", "").lower()

            # Calculate similarity scores
            key_similarity = self._string_similarity(target_key.lower(), source_key.lower())
            label_similarity = self._string_similarity(target_label, source_label)
            desc_similarity = self._string_similarity(target_desc, source_desc)

            # Weighted average (keys and labels are more important than descriptions)
            overall_score = key_similarity * 0.5 + label_similarity * 0.3 + desc_similarity * 0.2

            # Only include if there's some meaningful similarity
            if overall_score > 0.3:
                scores.append((source_key, overall_score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def _string_similarity(self, a: str, b: str) -> float:
        """
        Calculate similarity between two strings (0-1).
        Uses SequenceMatcher ratio.
        """
        return SequenceMatcher(None, a, b).ratio()

    def validate_mapping(
        self, source_phase: PhaseDefinition, target_phase: PhaseDefinition, mapping: Dict[str, str]
    ) -> Tuple[bool, List[str]]:
        """
        Validate that a mapping is valid (all target inputs can be satisfied).

        Returns:
            (is_valid, list_of_issues)
        """
        issues = []

        # Check that all mapped target keys exist in target inputs
        for target_key, source_key in mapping.items():
            if target_key not in target_phase.input_schema:
                issues.append(f"Target input '{target_key}' not found in {target_phase.name}")

            if source_key not in source_phase.output_schema:
                issues.append(f"Source output '{source_key}' not found in {source_phase.name}")

        # Check that all required target inputs have a source
        for target_key, target_input in target_phase.input_schema.items():
            if target_input.required and target_key not in mapping:
                issues.append(
                    f"Required input '{target_key}' in {target_phase.name} "
                    f"cannot be auto-mapped from {source_phase.name} outputs"
                )

        return len(issues) == 0, issues


def build_full_phase_pipeline(
    phase_names: List[str], user_mappings: Optional[Dict[str, Dict[str, str]]] = None
) -> Dict[str, Dict[str, str]]:
    """
    Build complete input mapping for all phases in a workflow.

    Args:
        phase_names: List of phase names in execution order
        user_mappings: User overrides {phase_index: {target_key: source_key}}

    Returns:
        Complete mapping: {phase_name: {input_key: output_key}}

    Raises:
        PhaseMappingError: If any phase mapping fails
    """
    if len(phase_names) < 1:
        raise PhaseMappingError("Workflow must have at least one phase")

    mapper = PhaseMapper()
    full_mapping = {}

    for i in range(1, len(phase_names)):  # Skip first phase (index 0)
        source_phase_name = phase_names[i - 1]
        target_phase_name = phase_names[i]

        # Get any user overrides for this phase
        phase_overrides = {}
        if user_mappings and target_phase_name in user_mappings:
            phase_overrides = user_mappings[target_phase_name]

        # Build mapping
        phase_mapping = mapper.map_phases(
            source_phase_name, target_phase_name, user_overrides=phase_overrides
        )

        full_mapping[target_phase_name] = phase_mapping

    logger.info(f"Built phase pipeline mapping for {len(phase_names)} phases")
    return full_mapping
