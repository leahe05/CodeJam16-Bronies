"""Ask anything (task C3): Claude drafts the patient's 2 answer choices for a nurse question.

    ask.make_options(question, context) -> {"options": [a, b], "source": "ai"}     (raises without a key)

Uses Paco's nurse_ai (lookat/nurse_ai.py). Live._suggest catches any error and uses the rule-based choices.
"""
from lookat import nurse_ai


def make_options(question: str, context: str = "") -> dict:
    return nurse_ai.make_options(question, context)


def suggest_questions(context: dict) -> dict:
    """Two ready-made questions for the nurse (raises without a key; Live falls back to fallback_questions)."""
    return {"questions": nurse_ai.suggest_questions(context), "source": "ai"}


def fallback_questions(alert: dict | None) -> list:
    """Rule-based questions when Claude is unavailable, chosen by what the open request is about."""
    text = f"{(alert or {}).get('text', '')} {(alert or {}).get('reason') or ''}".lower()
    kind = (alert or {}).get("kind")
    if kind == "call" or "call signal" in text or "eyebrow" in text:
        return [{"question": "Do you need help right away?", "options": ["Yes, now", "It can wait"]},
                {"question": "Is it pain or breathing?", "options": ["Yes", "No, something else"]}]
    if "blanket" in text or "cold" in text:
        return [{"question": "Would you like one blanket or two?", "options": ["One", "Two"]},
                {"question": "Should I turn the fan off too?", "options": ["Yes, please", "No, keep it on"]}]
    if "warm" in text or "hot" in text:
        return [{"question": "Would you like the fan on?", "options": ["Yes, please", "No, thanks"]},
                {"question": "Would a cool cloth help?", "options": ["Yes", "No"]}]
    if "water" in text or "drink" in text:
        return [{"question": "Would you like some water now?", "options": ["Yes, please", "Not now"]},
                {"question": "Water or juice?", "options": ["Water", "Juice"]}]
    if "position" in text or "pillow" in text:
        return [{"question": "Would you like to change position?", "options": ["Yes", "No"]},
                {"question": "Left side or right side?", "options": ["Left", "Right"]}]
    return [{"question": "Are you comfortable right now?", "options": ["Yes", "No"]},
            {"question": "Do you need me to come in?", "options": ["Yes, please", "Not now"]}]
