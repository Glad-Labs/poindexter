"""Scorers — capability-specific implementations of the ``Scorer`` Protocol.

Wave 1 ships ``RerankerScorer`` (deterministic nDCG/MRR). Later waves add
embedding/STT (deterministic) and judge/perceptual scorers behind the same
``services.model_eval.types.Scorer`` seam.
"""
