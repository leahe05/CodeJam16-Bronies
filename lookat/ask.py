"""Ask anything (task C3): Claude drafts the patient's 2 answer choices for a nurse question.

    ask.make_options(question, context) -> {"options": [a, b], "source": "ai"}     (raises without a key)

Uses Paco's nurse_ai (lookat/nurse_ai.py). Live._suggest catches any error and uses the rule-based choices.
"""
from lookat import nurse_ai


def make_options(question: str, context: str = "") -> dict:
    return nurse_ai.make_options(question, context)
