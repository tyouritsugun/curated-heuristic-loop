"""Shared constants for generator/evaluator guideline manuals."""

GUIDELINES_CATEGORY_CODE = "GLN"
GENERATOR_GUIDE_TITLE = "Generator workflow guidelines"
EVALUATOR_GUIDE_TITLE = "Evaluator workflow guidelines"
EVALUATOR_CPU_GUIDE_TITLE = "Evaluator workflow guidelines (CPU-only)"

EXPECTED_GUIDELINE_TITLES = {
    GENERATOR_GUIDE_TITLE,
    EVALUATOR_GUIDE_TITLE,
    EVALUATOR_CPU_GUIDE_TITLE,
}

__all__ = [
    "GUIDELINES_CATEGORY_CODE",
    "GENERATOR_GUIDE_TITLE",
    "EVALUATOR_GUIDE_TITLE",
    "EVALUATOR_CPU_GUIDE_TITLE",
    "EXPECTED_GUIDELINE_TITLES",
]
