"""Entry point for the Bright Smile Dental Clinic AI receptionist.

Run with:  python main.py
"""

import logging
import sys

from config import settings


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )


def _preflight_checks() -> None:
    """Validate that required configuration is present before starting."""
    missing: list[str] = []

    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")

    if settings.llm_provider == "anthropic" and not settings.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    elif settings.llm_provider == "openrouter" and not settings.openrouter_api_key:
        missing.append("OPENROUTER_API_KEY")
    elif settings.llm_provider == "openai" and not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")

    if missing:
        print("ERROR: The following environment variables are missing from .env:")
        for var in missing:
            print(f"  - {var}")
        print("\nCopy .env.example to .env and fill in the values.")
        sys.exit(1)


if __name__ == "__main__":
    _setup_logging()
    _preflight_checks()

    # Import here so that config errors surface in preflight first
    from telegram_bot import run_bot

    run_bot()