# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An AI receptionist harness for **Bright Smile Dental Clinic**. The system is a Python middle layer that interfaces an LLM with external communication channels and the Google Calendar API, structured around a dialogue state machine with inline menu buttons.

**Phase 1 (Current):** Telegram bot as the customer touchpoint.
**Phase 2 (Future):** WhatsApp via Twilio or Meta API Webhooks — core business logic must be reusable without rewriting.

**Website:** The clinic's one-page marketing site (Next.js 16 + Tailwind v4) was built in `website/` and later moved out of this repository — it is maintained in a separate workspace. All its CTAs deep-link into the Telegram bot (`@dental_cs_sg_bot`, with `?start=book_dr_<id>` per-dentist links).

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot (make sure .env is configured first)
python main.py

# Quick syntax check across all modules
python3 -m py_compile config.py guardrails.py session_manager.py llm_client.py state_machine.py template_manager.py calendar_service.py harness.py telegram_bot.py main.py
```

Google Calendar OAuth: place `credentials.json` (downloaded from Google Cloud Console) in the project root. On first run, a browser flow generates `token.json` for subsequent sessions.

## Tech Stack

- Python 3.10+
- `python-telegram-bot` — Telegram channel listener (inline keyboards, callback queries)
- `google-auth-oauthlib` + `google-api-python-client` — Google Calendar integration
- `openai` or `openrouter` or `anthropic` — LLM client (provider selected via `LLM_PROVIDER` env var)
- `python-dotenv` — configuration from `.env`

## Configuration

All config is loaded from `.env` via `config.py`. Copy `.env.example` to `.env` and fill in:
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `GOOGLE_CALENDAR_ID` — clinic calendar email or resource ID
- `LLM_PROVIDER` — `"openai"`, `"openrouter"`, or `"anthropic"`
- `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` — the chosen provider's key
- `CLINIC_OPEN_HOUR` / `CLINIC_CLOSE_HOUR` — operating hours (24h, default 8–17)
- `CLINIC_TIMEZONE` — default `Asia/Singapore`
- `APPOINTMENT_DURATION_MINUTES` — default 30
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_USE_TLS` — SMTP settings for sending appointment confirmation emails to patients

Static data (clinic info, doctors, pricing) is stored in JSON files under `data/`. Response text is stored in template files under `templates/`. Both are editable without code changes.

## Architecture

```
Telegram message / button press
        │
        ▼
┌─────────────────────────────────────────────┐
│        PYTHON APPLICATION HARNESS           │
│                                             │
│ 1. Hard Guardrail Filter                     │
│    (blocks medical triage keywords)          │
│ 2. State Machine                             │
│    (menu / form / slots / confirmation / …)  │
│ 3. LLM Intent Interpretation                 │
│    (maps free-form text to menu choices)     │
│ 4. Google Calendar actions                   │
│ 5. Template rendering                        │
│    (all responses from files)                │
│ 6. Route response + keyboard to channel      │
└─────────────────────────────────────────────┘
```

### Module layout & responsibilities

- **`main.py`** — Entry point. Runs preflight config checks, then starts the Telegram bot.
- **`config.py`** — Loads `.env` into a frozen `Settings` dataclass. Module-level `settings` singleton imported everywhere.
- **`guardrails.py`** — Pre-LLM keyword filter. Two tiers: critical emergency keywords (breathing difficulty, uncontrolled bleeding, severe facial swelling) → immediate A&E redirect; general medical keywords (pain, bleeding, emergency, swelling, toothache, antibiotic) → emergency response template.
- **`session_manager.py`** — Per-user session tracking. Stores conversation history (rolling 20-message window), current state machine state, collected form data, form step, retry count, and selected doctor/date/time. `UserSession` class per user.
- **`state_machine.py`** — Dialogue state machine. Defines all conversation states (menu, form_collection, doctor_select, slots, confirmation, static_display, triage, handoff), their templates, options, and transitions. Loads doctor data from `data/doctors.json` and clinic info from `data/clinic.json`.
- **`llm_client.py`** — LLM abstraction. Two functions: `get_llm_response()` for calendar intent extraction (structured JSON), `interpret_menu_choice()` for mapping free-form user input to menu options. Provider selected at runtime via `settings.llm_provider`.
- **`template_manager.py`** — Loads `.txt` template files from `templates/` and renders them with `{variable}` substitution. Templates cached in memory; `reload_all()` clears cache for hot-reloading during development.
- **`calendar_service.py`** — Google Calendar integration. OAuth via `credentials.json`/`token.json`. `check_availability(date)` returns busy blocks + computed free slots. `book_appointment(date, time, patient_name)` creates a calendar event in the clinic timezone.
- **`email_service.py`** — SMTP email confirmation service. Sends appointment confirmation emails to patients after booking or reschedule. Uses built-in `smtplib` (no external deps). Email body loaded from template files (`email_booking_confirmation.txt`, `email_reschedule_confirmation.txt`) so wording is editable without code changes. Best-effort: failures are logged but never break the booking flow.
- **`telegram_bot.py`** — Thin channel layer. Handles Telegram text messages and inline button callbacks, forwards to `harness.process_message()`, renders replies with inline keyboards. Separated from core logic for future WhatsApp swap.
- **`harness.py`** — Core orchestration. `process_message(user_id, text, callback_data)` runs the pipeline: guardrail → state machine → LLM (if needed) → calendar action → email confirmation → template rendering. Returns a `HarnessResult` with text and keyboard data. Channel-agnostic.

### Data files (`data/`)

- **`clinic.json`** — Clinic name, address, MRT details, operating hours, CHAS/MediSave flags, phone number.
- **`doctors.json`** — Doctor list with ID, name, specialty, bio, `calendar_id_env` (env var name for per-doctor Google Calendar ID), and per-day schedule (time slots in HH:MM).
- **`pricing.json`** — Procedure pricing ranges (consultation, scaling, fillings, wisdom tooth surgery, veneers, whitening, implants) with MediSave claimability flags.

### Template files (`templates/`)

All bot responses are stored as `.txt` files with `{variable}` placeholders. Developers can modify wording without touching code. Key templates: `welcome.txt`, `appointment_menu.txt`, `doctor_selection.txt`, `date_time_slot.txt`, `confirmation.txt`, `booking_success.txt`, `emergency_triage.txt`, `pricing.txt`, etc. Email templates: `email_booking_confirmation.txt`, `email_reschedule_confirmation.txt` (first line `Subject: ...` is extracted as the email subject; remaining lines form the body).

### Dialogue flow (state machine)

The bot follows a state-machine dialogue flow with these main branches:
1. **Book / Manage Appointments** → patient status → form collection → doctor selection → date/time slot selection → confirmation → booking
2. **Costs / Subsidies / Insurance** → static pricing/subsidy/insurance displays
3. **Dental Pain & Emergencies** → triage menu → fast-track booking or normal appointment flow
4. **Location & Opening Hours** → static display
5. **Live Agent Transfer** → handoff message, end session

### Doctor selection

When booking, users can choose a specific dentist (from `data/doctors.json`) or select "No Preference" for flexible assignment. The selected doctor's schedule is used to filter available time slots, combined with Google Calendar availability checks.

### Separation of concerns

Channel listener logic (`telegram_bot.py`) is kept distinct from calendar tooling (`calendar_service.py`), state machine (`state_machine.py`), and core orchestration (`harness.py`). To add WhatsApp, create a new `whatsapp_webhook.py` that calls `harness.process_message()` — no changes needed in the harness, state machine, or calendar modules.

### Safety guardrails (pre-LLM)

Before any message reaches the LLM, `guardrails.check_guardrail()` scans for medical triage keywords. Two tiers: critical keywords (breathing difficulty, facial swelling to eye/neck, uncontrolled bleeding) and general medical keywords (pain, bleeding, emergency, swelling, toothache, antibiotic). Matching messages are intercepted with a template response. The LLM is never invoked for these — no tokens consumed.

### LLM intent protocol

The LLM serves two roles:
1. **Menu navigation** — `interpret_menu_choice()` maps free-form text like "I want to book" to a menu option when the user types instead of pressing a button.
2. **Calendar intent** — `get_llm_response()` extracts structured JSON: `{"action": "check", "date": "YYYY-MM-DD"}` or `{"action": "book", "date": "YYYY-MM-DD", "time": "HH:MM"}`.

The harness gracefully handles JSON decoding failures — if the LLM returns unstructured text, it's passed through as a conversational reply.

### Session context

`SessionManager` maintains per-user sessions tracking: conversation history (rolling 20-message window), current state machine state, collected form data (patient name, phone, visit reason, doctor selection, date/time), retry count, and selected doctor/date/time. This enables multi-turn interactions like checking availability then booking based on the result.