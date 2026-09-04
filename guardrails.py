"""Pre-LLM safety guardrail filter.

Scans incoming messages for medical triage keywords and intercepts them
before the LLM is ever invoked, eliminating medical liability and saving
token consumption.

Two tiers of interception:
1. Critical emergency keywords → immediate A&E redirect
2. General medical keywords → emergency response with clinic phone number
"""

import re

# ── Critical keywords — immediate A&E redirect ──────────────────────
CRITICAL_KEYWORDS: list[str] = [
    "breathing difficulty",
    "difficulty breathing",
    "difficulty swallowing",
    "uncontrolled bleeding",
    "severe facial swelling",
    "swelling to the eye",
    "swelling to the neck",
    "swelling spreading to the eye",
    "swelling spreading to the neck",
]

# ── General medical keywords — emergency response ────────────────────
TRIGGER_KEYWORDS: list[str] = [
    "pain",
    "bleeding",
    "emergency",
    "swelling",
    "toothache",
    "antibiotic",
]

# Case-insensitive patterns
_CRITICAL_PATTERN = re.compile(
    r"(?:" + "|".join(re.escape(kw) for kw in CRITICAL_KEYWORDS) + r")",
    re.IGNORECASE,
)

_GENERAL_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in TRIGGER_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def contains_critical_keyword(text: str) -> bool:
    """Return True if *text* contains any critical emergency keyword."""
    return bool(_CRITICAL_PATTERN.search(text))


def contains_medical_keyword(text: str) -> bool:
    """Return True if *text* contains any general medical keyword."""
    return bool(_GENERAL_PATTERN.search(text))


def check_guardrail(text: str) -> str | None:
    """Run the guardrail check.

    Returns a template name string if a trigger is found (to be rendered
    by the template manager), or ``None`` if the message is safe to pass
    through to the LLM / state machine.

    Returns:
        "emergency_response" — for general medical keywords
        None — if the message is safe
    """
    if contains_critical_keyword(text):
        return "emergency_response"
    if contains_medical_keyword(text):
        return "emergency_response"
    return None