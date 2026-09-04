"""Dialogue state machine for the dental clinic chatbot.

Defines all conversation states, their types, transitions, and the
template names used for rendering. The harness module drives the
actual flow by reading these definitions and processing user input.

State types:
    menu             — Show numbered/button options, user picks one
    form_collection  — Collect multiple fields step by step
    doctor_select    — Show doctor list + "No Preference" option
    slots            — Collect date/time, then check calendar availability
    confirmation     — Yes/no choice with collected data summary
    static_display   — Show info, then return to a specified state
    triage           — Emergency triage menu (similar to menu)
    handoff          — Transfer to human agent, end session
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ── State IDs ────────────────────────────────────────────────────────

S_WELCOME = "1_WELCOME"
S_APPOINTMENT_MENU = "2_1_APPOINTMENT_MENU"
S_PATIENT_STATUS = "2_1_1_PATIENT_STATUS"
S_NEW_FLOW = "2_1_1_NEW_FLOW"
S_EXISTING_FLOW = "2_1_1_EXISTING_FLOW"
S_DOCTOR_SELECTION = "2_1_1_DOCTOR_SELECTION"
S_DOCTOR_SELECTION_MODIFY = "2_1_1_DOCTOR_SELECTION_MODIFY"
S_DATE_TIME_SLOT = "2_1_1_DATE_TIME_SLOT"
S_CONFIRMATION = "2_1_1_CONFIRMATION"
S_NO_SLOTS = "2_1_1_NO_SLOTS"
S_BOOKING_SUCCESS = "2_1_1_SUCCESS"
S_MODIFY_AUTH = "2_1_2_MODIFY_AUTH"
S_CANCEL_AUTH = "2_1_3_CANCEL_AUTH"
S_CONFIRM_CANCEL = "2_1_3_CONFIRM_CANCEL"
S_CANCEL_SUCCESS = "2_1_3_CANCEL_SUCCESS"
S_ERROR_NOT_FOUND = "2_1_ERROR_NOT_FOUND"
S_COSTS_MENU = "2_2_COSTS_MENU"
S_PRICING = "2_2_1_PRICING"
S_SUBSIDIES = "2_2_2_SUBSIDIES"
S_INSURANCE = "2_2_3_INSURANCE"
S_EMERGENCY_TRIAGE = "2_3_EMERGENCY_TRIAGE"
S_FAST_TRACK = "2_3_FAST_TRACK_BOOKING"
S_TRIAGE_ACK = "2_3_TRIAGE_ACK"
S_LOCATION_INFO = "2_4_LOCATION_INFO"
S_LIVE_AGENT = "2_5_LIVE_AGENT_TRANSFER"

INITIAL_STATE = S_WELCOME

# ── Data paths ───────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"

# ── State definitions ───────────────────────────────────────────────

STATES: dict[str, dict[str, Any]] = {
    # ── Main Menu ────────────────────────────────────────────────
    S_WELCOME: {
        "type": "menu",
        "template": "welcome",
        "options": [
            {"label": "📅 Book / Manage Appointments", "data": "1", "next": S_APPOINTMENT_MENU},
            {"label": "💰 Costs, Subsidies & Insurance", "data": "2", "next": S_COSTS_MENU},
            {"label": "🚨 Dental Pain & Emergencies", "data": "3", "next": S_EMERGENCY_TRIAGE},
            {"label": "📍 Location & Opening Hours", "data": "4", "next": S_LOCATION_INFO},
            {"label": "🗣️ Speak to a Human Receptionist", "data": "5", "next": S_LIVE_AGENT},
        ],
    },

    # ── Appointment Sub-menu ─────────────────────────────────────
    S_APPOINTMENT_MENU: {
        "type": "menu",
        "template": "appointment_menu",
        "options": [
            {"label": "🆕 Book a New Appointment", "data": "1.1", "next": S_DOCTOR_SELECTION},
            {"label": "🔄 Reschedule an Appointment", "data": "1.2", "next": S_MODIFY_AUTH},
            {"label": "❌ Cancel an Appointment", "data": "1.3", "next": S_CANCEL_AUTH},
            {"label": "⬅️ Return to Main Menu", "data": "1.4", "next": S_WELCOME},
        ],
    },

    # ── Doctor Selection (new booking) ───────────────────────────
    # Doctor is asked FIRST, before patient status and date/time
    S_DOCTOR_SELECTION: {
        "type": "doctor_select",
        "template": "doctor_selection",
        "next": S_PATIENT_STATUS,
        "back": S_APPOINTMENT_MENU,
    },

    # ── Doctor Selection (reschedule) ────────────────────────────
    # Separate state so it routes to date/time after selection
    S_DOCTOR_SELECTION_MODIFY: {
        "type": "doctor_select",
        "template": "doctor_selection",
        "next": S_DATE_TIME_SLOT,
        "back": S_MODIFY_AUTH,
    },

    # ── Patient Status ───────────────────────────────────────────
    S_PATIENT_STATUS: {
        "type": "menu",
        "template": "patient_status",
        "options": [
            {"label": "Yes, I have visited before", "data": "yes", "next": S_EXISTING_FLOW},
            {"label": "No, I'm a new patient", "data": "no", "next": S_NEW_FLOW},
            {"label": "⬅️ Back", "data": "back", "next": S_DOCTOR_SELECTION},
        ],
    },

    # ── New Patient Flow ─────────────────────────────────────────
    S_NEW_FLOW: {
        "type": "form_collection",
        "steps": [
            {"template": "new_patient_name", "variable": "patient_name"},
            {"template": "new_patient_phone", "variable": "patient_phone", "validation": r"^[89][0-9]{7}$"},
            {"template": "patient_email", "variable": "patient_email", "validation": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"},
            {"template": "new_patient_reason", "variable": "visit_reason"},
        ],
        "next": S_DATE_TIME_SLOT,
    },

    # ── Existing Patient Flow ────────────────────────────────────
    S_EXISTING_FLOW: {
        "type": "form_collection",
        "steps": [
            {"template": "existing_patient_id", "variable": "patient_identifier"},
            {"template": "patient_email", "variable": "patient_email", "validation": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"},
        ],
        "next": S_DATE_TIME_SLOT,
    },

    # ── Date/Time Slot Selection ─────────────────────────────────
    S_DATE_TIME_SLOT: {
        "type": "slots",
        "template": "date_time_slot",
        "action": "check_availability",
        "success_state": S_CONFIRMATION,
        "fail_state": S_NO_SLOTS,
        "back": S_DOCTOR_SELECTION,
    },

    # ── No Slots Available ───────────────────────────────────────
    S_NO_SLOTS: {
        "type": "menu",
        "template": "no_slots",
        "options": [
            {"label": "📅 Try a different date", "data": "retry", "next": S_DATE_TIME_SLOT},
            {"label": "🦷 Choose a different dentist", "data": "change_doctor", "next": S_DOCTOR_SELECTION},
            {"label": "⬅️ Main Menu", "data": "home", "next": S_WELCOME},
        ],
    },

    # ── Booking Confirmation ─────────────────────────────────────
    S_CONFIRMATION: {
        "type": "confirmation",
        "template": "confirmation",
        "confirm_action": "create_booking",
        "confirm_state": S_BOOKING_SUCCESS,
        "deny_state": S_DATE_TIME_SLOT,
    },

    # ── Booking Success ──────────────────────────────────────────
    S_BOOKING_SUCCESS: {
        "type": "static_display",
        "template": "booking_success",
        "next": S_WELCOME,
        "show_return": True,
    },

    # ── Reschedule (Modify) Auth ──────────────────────────────────
    S_MODIFY_AUTH: {
        "type": "form_collection",
        "steps": [
            {"template": "modify_auth", "variable": "modify_target"},
            {"template": "patient_email", "variable": "patient_email", "validation": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"},
        ],
        "action": "fetch_appointment",
        "next": S_DOCTOR_SELECTION_MODIFY,
        "fail_state": S_ERROR_NOT_FOUND,
    },

    # ── Cancel Auth ───────────────────────────────────────────────
    S_CANCEL_AUTH: {
        "type": "form_collection",
        "steps": [
            {"template": "cancel_auth", "variable": "cancel_target"},
        ],
        "action": "fetch_appointment",
        "next": S_CONFIRM_CANCEL,
        "fail_state": S_ERROR_NOT_FOUND,
    },

    # ── Confirm Cancellation ──────────────────────────────────────
    S_CONFIRM_CANCEL: {
        "type": "confirmation",
        "template": "confirm_cancel",
        "confirm_action": "delete_booking",
        "confirm_state": S_CANCEL_SUCCESS,
        "deny_state": S_WELCOME,
    },

    # ── Cancel Success ────────────────────────────────────────────
    S_CANCEL_SUCCESS: {
        "type": "static_display",
        "template": "cancel_success",
        "next": S_WELCOME,
        "show_return": True,
    },

    # ── Error: Not Found ──────────────────────────────────────────
    S_ERROR_NOT_FOUND: {
        "type": "menu",
        "template": "error_not_found",
        "options": [
            {"label": "🔄 Try Again", "data": "retry", "next": S_APPOINTMENT_MENU},
            {"label": "⬅️ Main Menu", "data": "home", "next": S_WELCOME},
        ],
    },

    # ── Costs Sub-menu ───────────────────────────────────────────
    S_COSTS_MENU: {
        "type": "menu",
        "template": "costs_menu",
        "options": [
            {"label": "💵 General Price Estimates", "data": "2.1", "next": S_PRICING},
            {"label": "💳 CHAS / Merdeka / Pioneer Subsidies", "data": "2.2", "next": S_SUBSIDIES},
            {"label": "🛡️ Corporate & Private Insurance Partners", "data": "2.3", "next": S_INSURANCE},
            {"label": "⬅️ Return to Main Menu", "data": "2.4", "next": S_WELCOME},
        ],
    },

    S_PRICING: {
        "type": "static_display",
        "template": "pricing",
        "next": S_COSTS_MENU,
    },

    S_SUBSIDIES: {
        "type": "static_display",
        "template": "subsidies",
        "next": S_COSTS_MENU,
    },

    S_INSURANCE: {
        "type": "static_display",
        "template": "insurance",
        "next": S_COSTS_MENU,
    },

    # ── Emergency Triage ─────────────────────────────────────────
    S_EMERGENCY_TRIAGE: {
        "type": "triage",
        "template": "emergency_triage",
        "options": [
            {"label": "⚡ Severe, throbbing toothache preventing sleep", "data": "3.1", "next": S_FAST_TRACK},
            {"label": "💥 Knocked-out or severely broken adult tooth", "data": "3.2", "next": S_FAST_TRACK},
            {"label": "🦷 Loose/lost filling or crown with mild sensitivity", "data": "3.3", "next": S_PATIENT_STATUS},
            {"label": "📞 Speak to emergency response staff", "data": "3.4", "next": S_LIVE_AGENT},
            {"label": "⬅️ Main Menu", "data": "back", "next": S_WELCOME},
        ],
    },

    S_FAST_TRACK: {
        "type": "form_collection",
        "steps": [
            {"template": "fast_track_booking", "variable": "emergency_phone"},
        ],
        "action": "system_alert",
        "next": S_TRIAGE_ACK,
    },

    S_TRIAGE_ACK: {
        "type": "static_display",
        "template": "triage_ack",
        "next": S_WELCOME,
        "show_return": True,
    },

    # ── Location Info ─────────────────────────────────────────────
    S_LOCATION_INFO: {
        "type": "static_display",
        "template": "location_info",
        "next": S_WELCOME,
    },

    # ── Live Agent Handoff ───────────────────────────────────────
    S_LIVE_AGENT: {
        "type": "handoff",
        "template": "live_agent",
        "end_session": True,
    },
}


# ── Helper functions ────────────────────────────────────────────────

def get_state(state_id: str) -> dict[str, Any]:
    """Return the state definition dict for *state_id*."""
    return STATES[state_id]


def get_state_type(state_id: str) -> str:
    """Return the type of a state (menu, form_collection, etc.)."""
    return STATES[state_id].get("type", "")


def get_initial_state() -> str:
    """Return the starting state ID."""
    return INITIAL_STATE


def is_end_state(state_id: str) -> bool:
    """Check if reaching this state ends the session."""
    return STATES[state_id].get("end_session", False)


# ── Doctor data helpers ─────────────────────────────────────────────

_doctors_cache: list[dict] | None = None


def load_doctors() -> list[dict]:
    """Load the doctor list from data/doctors.json (cached)."""
    global _doctors_cache
    if _doctors_cache is None:
        path = DATA_DIR / "doctors.json"
        _doctors_cache = json.loads(path.read_text(encoding="utf-8"))
    return _doctors_cache


def get_doctor(doctor_id: str) -> dict | None:
    """Find a doctor by ID. Returns None if not found."""
    for doc in load_doctors():
        if doc["id"] == doctor_id:
            return doc
    return None


def get_doctor_calendar_id(doctor_id: str | None) -> str:
    """Get the Google Calendar ID for a specific doctor.

    Doctors store their calendar ID via the ``calendar_id_env`` field in
    doctors.json, which names an environment variable (e.g.
    ``GOOGLE_CALENDAR_ID_1``).  The env var is looked up on the ``settings``
    singleton.  If the resolved value is ``"primary"`` or the doctor has
    no ``calendar_id_env`` field, fall back to the default
    ``GOOGLE_CALENDAR_ID``.
    """
    from config import settings

    if doctor_id:
        doc = get_doctor(doctor_id)
        if doc:
            env_name = doc.get("calendar_id_env")
            if env_name:
                # Map env-var name to the corresponding Settings attribute
                attr = env_name.lower()  # GOOGLE_CALENDAR_ID_1 → google_calendar_id_1
                cal_id = getattr(settings, attr, None)
                if cal_id and cal_id != "primary":
                    return cal_id
            # Legacy: fall back to calendar_id field if present
            legacy = doc.get("calendar_id")
            if legacy and legacy != "primary":
                return legacy
    return settings.google_calendar_id


def get_doctor_options() -> list[dict]:
    """Build the inline-keyboard options for the doctor selection state.

    Returns a list of {label, data, doctor_id} dicts. The last option is
    always "No Preference" (flexible assignment).
    """
    options = []
    for doc in load_doctors():
        options.append({
            "label": f"{doc['name']} — {doc['specialty']}",
            "data": doc["id"],
            "doctor_id": doc["id"],
        })
    options.append({
        "label": "🦷 No Preference (assign best available)",
        "data": "flexible",
        "doctor_id": None,
    })
    return options


# ── Clinic data helper ──────────────────────────────────────────────

_clinic_cache: dict | None = None


def load_clinic_info() -> dict:
    """Load clinic information from data/clinic.json (cached)."""
    global _clinic_cache
    if _clinic_cache is None:
        path = DATA_DIR / "clinic.json"
        _clinic_cache = json.loads(path.read_text(encoding="utf-8"))
    return _clinic_cache


def load_pricing() -> dict:
    """Load pricing data from data/pricing.json (not cached — small file)."""
    path = DATA_DIR / "pricing.json"
    return json.loads(path.read_text(encoding="utf-8"))