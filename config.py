"""Centralised configuration loaded from environment variables via .env."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str

    # Google Calendar
    google_calendar_id: str
    google_calendar_id_1: str
    google_calendar_id_2: str
    google_calendar_id_3: str

    # LLM
    llm_provider: str  # "openai", "openrouter", or "anthropic"
    openai_api_key: str
    openai_model: str
    openrouter_api_key: str
    openrouter_model: str
    anthropic_api_key: str
    anthropic_model: str

    # Clinic hours & timezone
    clinic_open_hour: int
    clinic_close_hour: int
    clinic_timezone: str
    appointment_duration_minutes: int

    # Email (SMTP)
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_use_tls: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
            google_calendar_id_1=os.getenv("GOOGLE_CALENDAR_ID_1", "primary"),
            google_calendar_id_2=os.getenv("GOOGLE_CALENDAR_ID_2", "primary"),
            google_calendar_id_3=os.getenv("GOOGLE_CALENDAR_ID_3", "primary"),
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            clinic_open_hour=int(os.getenv("CLINIC_OPEN_HOUR", "8")),
            clinic_close_hour=int(os.getenv("CLINIC_CLOSE_HOUR", "17")),
            clinic_timezone=os.getenv("CLINIC_TIMEZONE", "Asia/Singapore"),
            appointment_duration_minutes=int(os.getenv("APPOINTMENT_DURATION_MINUTES", "30")),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            smtp_use_tls=os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes"),
        )


# Module-level singleton — import this everywhere
settings = Settings.from_env()