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


def _topic_questions(text: str):
    text = text.lower()
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
    if "pain" in text or "breath" in text:
        return [{"question": "Is it getting worse?", "options": ["Yes", "No"]},
                {"question": "Should I come right now?", "options": ["Yes, now", "In a few minutes"]}]
    return None


def fallback_questions(alert: dict | None, answer: str | None = None, asked: str | None = None) -> list:
    """Rule-based questions when Claude is unavailable: a follow-up to the patient's latest answer if it names a
    topic, else by what the open request is about. Never the question just asked."""
    text = f"{(alert or {}).get('text', '')} {(alert or {}).get('reason') or ''}".lower()
    kind = (alert or {}).get("kind")
    out = _topic_questions(answer or "")
    if out is None and (kind == "call" or "call signal" in text or "eyebrow" in text):
        out = [{"question": "Do you need help right away?", "options": ["Yes, now", "It can wait"]},
               {"question": "Is it pain or breathing?", "options": ["Yes", "No, something else"]}]
    out = out or _topic_questions(text) or \
        [{"question": "Are you comfortable right now?", "options": ["Yes", "No"]},
         {"question": "Do you need me to come in?", "options": ["Yes, please", "Not now"]}]
    if asked and any(q["question"].casefold() == asked.casefold() for q in out):
        out = [q for q in out if q["question"].casefold() != asked.casefold()] + \
              [{"question": "Is there anything else you need?", "options": ["Yes", "No, thank you"]}]
    return out
