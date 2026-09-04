"""LLM client abstraction.

The LLM serves two roles:
1. Intent interpretation — mapping free-form user input to menu choices
   (e.g. "I want to book" → option 1, "how much does it cost" → option 2)
2. Calendar intent extraction — when the user provides date/time in
   natural language, the LLM extracts structured JSON for the harness.

Sends the system prompt + conversation history + user message to the LLM
and parses the response. Gracefully handles cases where the LLM returns
plain text instead of JSON.
"""

import json
import logging
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

# The system prompt that constrains the LLM to structured JSON output
SYSTEM_PROMPT = """\
You are an AI receptionist for "Bright Smile Dental Clinic". Your only job is to help patients check open slots and schedule appointments.

CRITICAL RULES:
1. You do not have direct access to the calendar. You must output structured commands to instruct the harness to interact with the calendar.
2. If the user wants to check a date, you must respond strictly with this JSON format: {"action": "check", "date": "YYYY-MM-DD"}
3. If the user wants to book an appointment, you must respond strictly with this JSON format: {"action": "book", "date": "YYYY-MM-DD", "time": "HH:MM"}
4. If they are making normal polite conversation, reply with standard professional text.
5. NEVER give medical advice, diagnose symptoms, or suggest medications. If a user asks about health complications, politely inform them you are connecting them to human medical staff.
6. The current year is 2026."""


# Prompt for intent interpretation — used when the harness needs to map
# free-form user input to a menu option number.
INTENT_INTERPRETATION_PROMPT = """\
You are a menu navigation assistant for Bright Smile Dental Clinic. \
The user is presented with a menu and you must determine which option they are selecting.

Current menu options:
{menu_options}

User input: "{user_input}"

Respond with ONLY a JSON object: {{"option": "<option_data>"}} where option_data is the data value of the matching option. \
If the user's input doesn't clearly match any option, respond with: {{"option": null}}. \
Do not include any other text."""


class LLMResponse:
    """Parsed LLM response — either a structured intent or plain text."""

    def __init__(self, text: str, intent: dict[str, Any] | None = None) -> None:
        self.raw_text = text
        self.intent = intent
        self.is_intent = intent is not None

    @property
    def action(self) -> str | None:
        return self.intent.get("action") if self.intent else None

    @property
    def date(self) -> str | None:
        return self.intent.get("date") if self.intent else None

    @property
    def time(self) -> str | None:
        return self.intent.get("time") if self.intent else None


def _parse_response(raw: str) -> LLMResponse:
    """Try to extract a JSON intent from the LLM's raw text output."""
    text = raw.strip()

    # Fast path: the whole response is JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return LLMResponse(text=raw, intent=data)
    except json.JSONDecodeError:
        pass

    # Slow path: extract the first JSON object from the text
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return LLMResponse(text=raw, intent=data)
                    except json.JSONDecodeError:
                        pass
                    break

    # No valid JSON found — treat as conversational text
    logger.debug("LLM returned non-JSON response: %s", raw[:120])
    return LLMResponse(text=raw, intent=None)


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _call_openai(history: list[dict[str, str]], user_message: str) -> str:
    """Invoke an OpenAI chat completion and return the raw text."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def _call_openrouter(history: list[dict[str, str]], user_message: str) -> str:
    """Invoke a chat completion via OpenRouter (OpenAI-compatible API)."""
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=settings.openrouter_model,
        messages=messages,
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def _call_anthropic(history: list[dict[str, str]], user_message: str) -> str:
    """Invoke an Anthropic messages API call and return the raw text."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    messages: list[dict[str, str]] = []
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    response = client.messages.create(
        model=settings.anthropic_model,
        system=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=512,
        temperature=0.2,
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return "".join(parts)


# ---------------------------------------------------------------------------
# Generic provider call (uses the system prompt for calendar intents)
# ---------------------------------------------------------------------------

def _call_llm(history: list[dict[str, str]], user_message: str, system_prompt: str = SYSTEM_PROMPT) -> str:
    """Call the configured LLM provider with a custom system prompt."""
    provider = settings.llm_provider.lower()

    if provider == "openrouter":
        from openai import OpenAI
        client = OpenAI(api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        response = client.chat.completions.create(model=settings.openrouter_model, messages=messages, temperature=0.2)
        return response.choices[0].message.content or ""

    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        messages: list[dict[str, str]] = []
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        response = client.messages.create(model=settings.anthropic_model, system=system_prompt, messages=messages, max_tokens=512, temperature=0.2)
        parts = [block.text for block in response.content if block.type == "text"]
        return "".join(parts)

    else:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        response = client.chat.completions.create(model=settings.openai_model, messages=messages, temperature=0.2)
        return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_llm_response(history: list[dict[str, str]], user_message: str) -> LLMResponse:
    """Send *user_message* with *history* to the configured LLM provider.

    Returns an :class:`LLMResponse` containing either a parsed intent dict
    or the raw conversational text.
    """
    if not _check_api_key():
        raise ValueError(f"{settings.llm_provider.upper()}_API_KEY is not set in .env")
    raw = _call_llm(history, user_message)
    return _parse_response(raw)


def interpret_menu_choice(user_input: str, menu_options: list[dict]) -> str | None:
    """Use the LLM to map free-form user input to a menu option.

    Args:
        user_input: What the user typed.
        menu_options: List of {label, data} dicts for the current menu.

    Returns the matched option's data string, or None if no match.
    """
    options_text = "\n".join(
        f"  {i+1}. {opt['label']} (data: {opt['data']})"
        for i, opt in enumerate(menu_options)
    )
    prompt = INTENT_INTERPRETATION_PROMPT.format(
        menu_options=options_text,
        user_input=user_input,
    )

    try:
        raw = _call_llm([], prompt, system_prompt="You are a menu navigation assistant. Respond only with JSON.")
        response = _parse_response(raw)
        if response.intent and "option" in response.intent:
            option_val = response.intent["option"]
            if option_val is not None:
                return str(option_val)
    except Exception as exc:
        logger.error("Menu interpretation failed: %s", exc)

    return None


def _check_api_key() -> bool:
    """Verify that the API key for the configured provider is set."""
    provider = settings.llm_provider.lower()
    if provider == "openrouter":
        return bool(settings.openrouter_api_key)
    elif provider == "anthropic":
        return bool(settings.anthropic_api_key)
    else:
        return bool(settings.openai_api_key)