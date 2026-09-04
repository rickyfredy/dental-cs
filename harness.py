"""Core harness orchestration.

Drives the dialogue state machine: processes user input (button callbacks
or free-form text), transitions between states, collects form data,
and calls calendar APIs when needed. All response text comes from
template files via the template manager.

Channel-agnostic — returns a :class:`HarnessResult` that the channel
(Telegram, WhatsApp, …) renders for the user.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import calendar_service
import email_service
import state_machine as sm
from guardrails import check_guardrail
from llm_client import get_llm_response, interpret_menu_choice
from session_manager import SessionManager, MAX_RETRIES
import template_manager as tm

logger = logging.getLogger(__name__)

# Shared session manager
sessions = SessionManager()


@dataclass
class HarnessResult:
    """Result returned by process_message for the channel to render."""

    text: str = ""
    keyboard: list[list[dict]] | None = None  # [[{label, data}, ...], ...]
    end_session: bool = False

    def has_keyboard(self) -> bool:
        return bool(self.keyboard)


# ── Public API ──────────────────────────────────────────────────────

def process_message(
    user_id: int | str,
    text: str | None = None,
    callback_data: str | None = None,
) -> HarnessResult:
    """Process an incoming user interaction and return a reply.

    Args:
        user_id: Unique user identifier.
        text: Free-form text the user typed (None if they pressed a button).
        callback_data: Data from an inline button press (None if they typed text).
    """
    session = sessions.get_session(user_id)

    # ── Stage 1: Guardrail (only for text input, not button presses) ──
    if text:
        guardrail_template = check_guardrail(text)
        if guardrail_template:
            logger.info("Guardrail intercepted message from user %s", user_id)
            clinic = sm.load_clinic_info()
            rendered = tm.render(guardrail_template, **clinic)
            sessions.add_user_message(user_id, text)
            sessions.add_assistant_message(user_id, rendered)
            return HarnessResult(text=rendered, end_session=True)

    # ── Initialise state if first interaction ────────────────────────
    if not session.state:
        session.state = sm.get_initial_state()

    # ── Stage 2: Process input based on current state type ──────────
    result = _process_state(user_id, session, text, callback_data)

    # ── Stage 3: Update conversation history ────────────────────────
    if text:
        sessions.add_user_message(user_id, text)
    if result.text:
        sessions.add_assistant_message(user_id, result.text)

    return result


def reset_session(user_id: int | str) -> HarnessResult:
    """Reset a user's session back to the main menu."""
    sessions.clear(user_id)
    return _render_state(user_id, sm.get_initial_state())


# ── State processing ────────────────────────────────────────────────

def _process_state(
    user_id: int | str,
    session,
    text: str | None,
    callback_data: str | None,
) -> HarnessResult:
    """Dispatch to the appropriate handler for the current state type."""
    state_id = session.state
    state = sm.get_state(state_id)
    state_type = state.get("type", "")

    # Global callback: "home" always returns to the main menu
    if callback_data == "home":
        session.reset_flow()
        return _transition(user_id, session, sm.S_WELCOME)

    # Global callback: "end" — show goodbye and end session
    if callback_data == "end":
        rendered = tm.render("goodbye", **sm.load_clinic_info())
        session.reset_all()
        return HarnessResult(text=rendered, end_session=True)

    if state_type == "menu":
        return _handle_menu(user_id, session, state, text, callback_data)
    elif state_type == "form_collection":
        return _handle_form(user_id, session, state, text, callback_data)
    elif state_type == "doctor_select":
        return _handle_doctor_select(user_id, session, state, text, callback_data)
    elif state_type == "slots":
        return _handle_slots(user_id, session, state, text, callback_data)
    elif state_type == "confirmation":
        return _handle_confirmation(user_id, session, state, text, callback_data)
    elif state_type == "static_display":
        return _handle_static_display(user_id, session, state)
    elif state_type == "triage":
        return _handle_menu(user_id, session, state, text, callback_data)
    elif state_type == "handoff":
        return _handle_handoff(user_id, session, state)
    else:
        logger.error("Unknown state type '%s' for state '%s'", state_type, state_id)
        return _render_fallback(user_id, session)


# ── Menu / Triage handler ───────────────────────────────────────────

def _handle_menu(user_id, session, state, text, callback_data) -> HarnessResult:
    options = state.get("options", [])

    # Try to match button press first
    if callback_data is not None:
        # Handle back/menu navigation
        if callback_data == "back":
            # Find the back state, or go to welcome
            back_state = state.get("back", sm.S_WELCOME)
            return _transition(user_id, session, back_state)
        # Handle "home" — always returns to main menu
        if callback_data == "home":
            session.reset_flow()
            return _transition(user_id, session, sm.S_WELCOME)
        matched = _find_option(options, callback_data)
        if matched:
            return _transition(user_id, session, matched["next"])

    # Try direct text match (user typed the option number or label)
    if text:
        matched = _find_option(options, text.strip())
        if matched:
            session.retry_count = 0
            return _transition(user_id, session, matched["next"])

        # Try LLM interpretation
        option_data = interpret_menu_choice(text, options)
        if option_data:
            matched = _find_option(options, option_data)
            if matched:
                session.retry_count = 0
                return _transition(user_id, session, matched["next"])

    # Fallback: increment retry, show not-understood message
    session.retry_count += 1
    if session.retry_count >= MAX_RETRIES:
        session.retry_count = 0
        rendered = tm.render("max_retries_exceeded")
        return HarnessResult(
            text=rendered,
            keyboard=None,
            end_session=True,
        )

    rendered = tm.render("intent_not_understood")
    # Re-show current menu
    current_render = _render_state(user_id, session.state)
    return HarnessResult(
        text=rendered + "\n\n" + current_render.text,
        keyboard=current_render.keyboard,
    )


def _find_option(options: list[dict], data: str) -> dict | None:
    """Find an option whose data matches the given string."""
    for opt in options:
        if opt["data"] == data:
            return opt
    return None


# ── Form Collection handler ─────────────────────────────────────────

def _handle_form(user_id, session, state, text, callback_data) -> HarnessResult:
    steps = state.get("steps", [])
    step_idx = session.form_step

    # If this is the first step (no input yet), show the first prompt
    if callback_data is None and text is None:
        return _render_form_prompt(user_id, session, state)

    # Collect the current step's variable
    if text is not None and step_idx < len(steps):
        step = steps[step_idx]
        var_name = step["variable"]

        # Validate if the step has a validation regex
        validation = step.get("validation")
        if validation:
            if not re.match(validation, text.strip()):
                # Re-show the prompt with a note about invalid input
                rendered = tm.render(step["template"], **sm.load_clinic_info())
                return HarnessResult(
                    text=f"Invalid input. Please check the format and try again.\n\n{rendered}",
                )

        session.form_data[var_name] = text.strip()
        session.form_step += 1

        # If there are more steps, show the next prompt
        if session.form_step < len(steps):
            return _render_form_prompt(user_id, session, state)

        # All steps complete — check if there's an action to execute
        action = state.get("action")
        if action == "fetch_appointment":
            # Try to fetch the appointment (dummy for now)
            found = _fetch_appointment(session.form_data)
            if found:
                # Merge fetched appointment data into form_data
                session.form_data.update(found)
                return _transition(user_id, session, state["next"])
            else:
                return _transition(user_id, session, state.get("fail_state", sm.S_ERROR_NOT_FOUND))

        if action == "system_alert":
            # Emergency fast-track: log and acknowledge
            logger.warning("EMERGENCY ALERT from user %s: %s", user_id, session.form_data)
            return _transition(user_id, session, state["next"])

        # No action — just transition
        return _transition(user_id, session, state["next"])

    # No text and no callback — show the prompt
    return _render_form_prompt(user_id, session, state)


def _render_form_prompt(user_id, session, state) -> HarnessResult:
    """Render the prompt for the current form step."""
    steps = state.get("steps", [])
    step_idx = session.form_step
    if step_idx < len(steps):
        step = steps[step_idx]
        rendered = tm.render(step["template"], **sm.load_clinic_info())
        return HarnessResult(text=rendered)
    # Shouldn't happen, but handle gracefully
    return _transition(user_id, session, state.get("next", sm.S_WELCOME))


# ── Doctor Selection handler ────────────────────────────────────────

def _handle_doctor_select(user_id, session, state, text, callback_data) -> HarnessResult:
    options = sm.get_doctor_options()

    # If no input yet, render the selection menu
    if callback_data is None and text is None:
        return _render_doctor_menu(state)

    # Match callback or text to a doctor option
    selected_data = callback_data or (text.strip() if text else None)
    if selected_data:
        # Check for back navigation
        if selected_data == "back":
            return _transition(user_id, session, state.get("back", sm.S_PATIENT_STATUS))

        # Match against doctor options
        for opt in options:
            if opt["data"] == selected_data:
                session.selected_doctor_id = opt.get("doctor_id")
                session.form_data["doctor_id"] = opt.get("doctor_id") or "flexible"
                # Get doctor name for display
                if opt.get("doctor_id"):
                    doc = sm.get_doctor(opt["doctor_id"])
                    session.form_data["doctor_name"] = doc["name"] if doc else "Any available dentist"
                else:
                    session.form_data["doctor_name"] = "Any available dentist"
                return _transition(user_id, session, state["next"])

    # Fallback
    session.retry_count += 1
    rendered = tm.render("intent_not_understood")
    menu = _render_doctor_menu(state)
    return HarnessResult(
        text=rendered + "\n\n" + menu.text,
        keyboard=menu.keyboard,
    )


def _render_doctor_menu(state) -> HarnessResult:
    """Render the doctor selection menu with inline keyboard."""
    clinic = sm.load_clinic_info()
    doctors = sm.load_doctors()
    doctor_list = "\n".join(
        f"  {i+1}. {d['name']} — {d['specialty']}" for i, d in enumerate(doctors)
    )
    rendered = tm.render(state["template"], doctor_list=doctor_list, **clinic)

    options = sm.get_doctor_options()
    keyboard = [[opt] for opt in [{"label": o["label"], "data": o["data"]} for o in options]]
    # Add back button
    keyboard.append([{"label": "⬅️ Back", "data": "back"}])

    return HarnessResult(text=rendered, keyboard=keyboard)


# ── Slots (Date/Time) handler ────────────────────────────────────────

def _handle_slots(user_id, session, state, text, callback_data) -> HarnessResult:
    """Collect date and time window, then check availability."""
    clinic = sm.load_clinic_info()

    # Handle slot selection callback (user picked a time from available slots)
    if callback_data and callback_data.startswith("slot:"):
        selected_time = callback_data.split(":", 1)[1]
        session.selected_time = selected_time
        session.form_data["selected_time"] = selected_time
        session.form_data["selected_date"] = session.form_data.get("slot_date", "")
        # Transition to confirmation state
        return _transition(user_id, session, state["success_state"])

    # Sub-step 0: No date collected yet — show date prompt
    if not session.form_data.get("slot_date"):
        if text:
            # User entered a date
            date_str = _parse_date(text.strip())
            if date_str:
                session.form_data["slot_date"] = date_str
                # Show time window selection
                rendered = tm.render(state["template"], **clinic)
                time_text = (
                    f"Got it — {date_str}. Now choose a time window:\n"
                    f"[1] Morning (9AM-12PM)\n"
                    f"[2] Afternoon (2PM-6PM)"
                )
                keyboard = [
                    [{"label": "🌅 Morning (9AM-12PM)", "data": "window:morning"}],
                    [{"label": "🌇 Afternoon (2PM-6PM)", "data": "window:afternoon"}],
                    [{"label": "⬅️ Back", "data": "back"}],
                ]
                return HarnessResult(text=time_text, keyboard=keyboard)
            else:
                rendered = tm.render(state["template"], **clinic)
                return HarnessResult(text=f"Sorry, I couldn't understand that date. Please use DD/MM/YYYY format.\n\n{rendered}")
        else:
            # Show date prompt
            rendered = tm.render(state["template"], **clinic)
            keyboard = [[{"label": "⬅️ Back", "data": "back"}]]
            return HarnessResult(text=rendered, keyboard=keyboard)

    # Sub-step 1: Date collected, waiting for time window
    if callback_data:
        if callback_data == "back":
            session.form_data.pop("slot_date", None)
            return _transition(user_id, session, state.get("back", sm.S_DOCTOR_SELECTION))

        if callback_data.startswith("window:"):
            window = callback_data.split(":", 1)[1]
            session.form_data["time_window"] = window
            # Check availability
            return _check_and_show_slots(user_id, session, state)

    if text:
        # User typed instead of pressing button — try to interpret
        text_lower = text.strip().lower()
        if "morning" in text_lower or text_lower == "1":
            session.form_data["time_window"] = "morning"
            return _check_and_show_slots(user_id, session, state)
        elif "afternoon" in text_lower or text_lower == "2":
            session.form_data["time_window"] = "afternoon"
            return _check_and_show_slots(user_id, session, state)

    # Fallback — re-show time window
    date_str = session.form_data.get("slot_date", "")
    time_text = (
        f"Got it — {date_str}. Now choose a time window:\n"
        f"[1] Morning (9AM-12PM)\n"
        f"[2] Afternoon (2PM-6PM)"
    )
    keyboard = [
        [{"label": "🌅 Morning (9AM-12PM)", "data": "window:morning"}],
        [{"label": "🌇 Afternoon (2PM-6PM)", "data": "window:afternoon"}],
        [{"label": "⬅️ Back", "data": "back"}],
    ]
    return HarnessResult(text=time_text, keyboard=keyboard)


def _check_and_show_slots(user_id, session, state) -> HarnessResult:
    """Check calendar availability and show available time slots."""
    date_str = session.form_data["slot_date"]
    window = session.form_data.get("time_window", "morning")
    doctor_id = session.selected_doctor_id

    # Determine time range based on window
    if window == "morning":
        start_hour, end_hour = 9, 12
    else:
        start_hour, end_hour = 14, 18

    # Get available slots from doctor's schedule and calendar
    available = _get_available_slots(date_str, doctor_id, start_hour, end_hour)

    if not available:
        doctor_name = session.form_data.get("doctor_name", "any dentist")
        # Transition to fail state and render it with form data
        session.state = state["fail_state"]
        fail_state = sm.get_state(state["fail_state"])
        clinic = sm.load_clinic_info()
        rendered = tm.render(
            fail_state.get("template", "no_slots"),
            selected_date=date_str,
            doctor_name=doctor_name,
            **clinic,
        )
        options = fail_state.get("options", [])
        keyboard = [[{"label": o["label"], "data": o["data"]}] for o in options] if options else None
        return HarnessResult(text=rendered, keyboard=keyboard)

    # Show available slots as inline keyboard
    doctor_name = session.form_data.get("doctor_name", "any dentist")
    slots_text = "\n".join(f"  • {s}" for s in available)
    rendered = tm.render(
        "slots_available",
        selected_date=date_str,
        doctor_name=doctor_name,
        available_slots=slots_text,
        **sm.load_clinic_info(),
    )

    # Build keyboard with slot buttons
    keyboard = [[{"label": f"🕐 {s}", "data": f"slot:{s}"}] for s in available]
    keyboard.append([{"label": "⬅️ Back", "data": "back"}])

    # Store available slots in form_data for later reference
    session.form_data["available_slots"] = available

    return HarnessResult(text=rendered, keyboard=keyboard)


def _get_available_slots(date_str: str, doctor_id: str | None, start_hour: int, end_hour: int) -> list[str]:
    """Get available time slots for a date, optionally filtered by doctor."""
    try:
        dt = datetime.fromisoformat(date_str)
    except ValueError:
        return []

    day_name = dt.strftime("%a").lower()  # mon, tue, etc.

    # Get doctor's available hours for this day
    if doctor_id:
        doctor = sm.get_doctor(doctor_id)
        if not doctor:
            return []
        doc_hours = doctor["schedule"].get(day_name, [])
    else:
        # Flexible — combine all doctors' schedules
        all_hours: set[str] = set()
        for doc in sm.load_doctors():
            all_hours.update(doc["schedule"].get(day_name, []))
        doc_hours = sorted(all_hours)

    # Filter by time window
    window_hours = []
    for h in doc_hours:
        hour_int = int(h.split(":")[0])
        if start_hour <= hour_int < end_hour:
            window_hours.append(h)

    if not window_hours:
        return []

    # Check Google Calendar for busy times
    try:
        cal_id = sm.get_doctor_calendar_id(doctor_id)
        result = calendar_service.check_availability(date_str, calendar_id=cal_id)
        busy_times = set()
        for block in result.get("busy", []):
            b_start = datetime.fromisoformat(block["start"])
            busy_times.add(b_start.strftime("%H:%M"))
    except Exception as exc:
        logger.warning("Calendar check failed, using doctor schedule only: %s", exc)
        busy_times = set()

    # Filter out busy slots
    available = [h for h in window_hours if h not in busy_times]
    return available


def _parse_date(text: str) -> str | None:
    """Parse various date formats into YYYY-MM-DD."""
    text = text.strip().lower()

    if text == "earliest":
        # Return tomorrow's date
        tomorrow = datetime.now() + timedelta(days=1)
        return tomorrow.strftime("%Y-%m-%d")

    # DD/MM/YYYY
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    # DD/MM (assume current year)
    match = re.match(r"^(\d{1,2})/(\d{1,2})$", text)
    if match:
        day, month = match.groups()
        year = datetime.now().year
        return f"{year}-{int(month):02d}-{int(day):02d}"

    # YYYY-MM-DD
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    return None


# ── Confirmation handler ─────────────────────────────────────────────

def _handle_confirmation(user_id, session, state, text, callback_data) -> HarnessResult:
    # Handle confirm/deny
    if callback_data in ("confirm", "yes") or (text and text.strip().lower() in ("confirm", "yes")):
        # Execute the action
        action = state.get("confirm_action")
        if action == "create_booking":
            return _execute_booking(user_id, session, state)
        elif action == "delete_booking":
            return _execute_cancellation(user_id, session, state)
        else:
            return _transition(user_id, session, state["confirm_state"])

    if callback_data in ("deny", "no", "change") or (text and text.strip().lower() in ("deny", "no", "change")):
        return _transition(user_id, session, state["deny_state"])

    # No input yet — show confirmation prompt
    return _render_confirmation(user_id, session, state)


def _render_confirmation(user_id, session, state) -> HarnessResult:
    """Render the confirmation prompt with collected data."""
    clinic = sm.load_clinic_info()
    rendered = tm.render(
        state["template"],
        patient_name=session.form_data.get("patient_name", "Patient"),
        doctor_name=session.form_data.get("doctor_name", "Any available dentist"),
        selected_date=session.form_data.get("selected_date", session.form_data.get("slot_date", "")),
        selected_time=session.form_data.get("selected_time", ""),
        visit_reason=session.form_data.get("visit_reason", "Consultation"),
        **clinic,
    )
    keyboard = [
        [{"label": "✅ Confirm", "data": "confirm"}],
        [{"label": "🔄 Change Details", "data": "deny"}],
    ]
    return HarnessResult(text=rendered, keyboard=keyboard)


def _execute_booking(user_id, session, state) -> HarnessResult:
    """Execute the calendar booking and transition to success state."""
    date_str = session.form_data.get("selected_date", "")
    time_str = session.form_data.get("selected_time", "")
    patient_name = session.form_data.get("patient_name", "Patient")
    doctor_name = session.form_data.get("doctor_name", "Any available dentist")
    visit_reason = session.form_data.get("visit_reason", "Consultation")
    patient_email = session.form_data.get("patient_email", "")

    # Determine if this is a reschedule (came from S_MODIFY_AUTH flow)
    is_reschedule = "modify_target" in session.form_data or "appt_date" in session.form_data

    clinic = sm.load_clinic_info()

    try:
        cal_id = sm.get_doctor_calendar_id(session.selected_doctor_id)
        calendar_service.book_appointment(date_str, time_str, patient_name, calendar_id=cal_id)
    except Exception as exc:
        logger.error("Booking failed: %s", exc, exc_info=True)
        return HarnessResult(
            text=f"I wasn't able to book that slot on {date_str} at {time_str}. It may have been taken. Would you like me to check availability for that date?"
        )

    # ── Send email confirmation (best-effort, never breaks booking) ──
    if patient_email:
        try:
            if is_reschedule:
                email_service.send_reschedule_confirmation(
                    patient_email=patient_email,
                    patient_name=patient_name,
                    doctor_name=doctor_name,
                    selected_date=date_str,
                    selected_time=time_str,
                    **clinic,
                )
            else:
                email_service.send_booking_confirmation(
                    patient_email=patient_email,
                    patient_name=patient_name,
                    doctor_name=doctor_name,
                    selected_date=date_str,
                    selected_time=time_str,
                    visit_reason=visit_reason,
                    **clinic,
                )
        except Exception as exc:
            logger.error("Email confirmation failed (non-blocking): %s", exc, exc_info=True)
    else:
        logger.info("No patient email on file — skipping confirmation email")

    rendered = tm.render(
        state.get("confirm_state") and sm.get_state(state["confirm_state"]).get("template", "booking_success") or "booking_success",
        doctor_name=doctor_name,
        selected_date=date_str,
        selected_time=time_str,
        **clinic,
    )

    session.state = state["confirm_state"]
    session.reset_flow()
    keyboard = [
        [{"label": "🏠 Return to Main Menu", "data": "home"}],
        [{"label": "👋 End Session", "data": "end"}],
    ]
    return HarnessResult(text=rendered, keyboard=keyboard)


def _execute_cancellation(user_id, session, state) -> HarnessResult:
    """Execute the cancellation and transition to cancel success."""
    clinic = sm.load_clinic_info()
    appt_date = session.form_data.get("appt_date", "")
    appt_time = session.form_data.get("appt_time", "")
    doctor_name = session.form_data.get("doctor_name", "your dentist")

    # In a real system, call calendar_service.delete_event() here
    logger.info("Cancelling appointment for user %s on %s at %s", user_id, appt_date, appt_time)

    rendered = tm.render(
        "cancel_success",
        appt_date=appt_date,
        appt_time=appt_time,
        doctor_name=doctor_name,
        **clinic,
    )

    session.state = state["confirm_state"]
    session.reset_flow()
    keyboard = [
        [{"label": "🏠 Return to Main Menu", "data": "home"}],
        [{"label": "👋 End Session", "data": "end"}],
    ]
    return HarnessResult(text=rendered, keyboard=keyboard)


# ── Static Display handler ───────────────────────────────────────────

def _handle_static_display(user_id, session, state) -> HarnessResult:
    """Show static content and transition to next state."""
    clinic = sm.load_clinic_info()
    # Merge clinic info with form data so templates can use collected variables
    render_vars = {**clinic, **session.form_data}
    rendered = tm.render(state["template"], **render_vars)
    next_state = state.get("next", sm.S_WELCOME)

    # If show_return, display content with Return to Main Menu + End Session buttons
    if state.get("show_return"):
        session.state = next_state
        session.reset_flow()
        keyboard = [
            [{"label": "🏠 Return to Main Menu", "data": "home"}],
            [{"label": "👋 End Session", "data": "end"}],
        ]
        return HarnessResult(text=rendered, keyboard=keyboard)

    # If end_session, just show the message
    if state.get("end_session"):
        session.state = next_state
        return HarnessResult(text=rendered, end_session=True)

    # Otherwise, show the content then auto-advance to the next state
    session.state = next_state
    session.form_step = 0
    next_render = _render_state(user_id, next_state)
    combined = rendered + "\n\n" + next_render.text
    return HarnessResult(
        text=combined,
        keyboard=next_render.keyboard,
    )


# ── Handoff handler ─────────────────────────────────────────────────

def _handle_handoff(user_id, session, state) -> HarnessResult:
    """Show the handoff message and end the session."""
    rendered = tm.render(state["template"], **sm.load_clinic_info())
    logger.info("Live agent handoff requested for user %s", user_id)
    return HarnessResult(text=rendered, end_session=True)


# ── Transition & rendering helpers ──────────────────────────────────

def _transition(user_id, session, next_state: str) -> HarnessResult:
    """Move to a new state, reset form progress, and render the new state."""
    session.state = next_state
    session.form_step = 0

    state = sm.get_state(next_state)
    state_type = state.get("type", "")

    # If transitioning to a form_collection state, render the first prompt
    if state_type == "form_collection":
        return _render_form_prompt(user_id, session, state)

    # If transitioning to a confirmation state, render with form data
    if state_type == "confirmation":
        return _render_confirmation(user_id, session, state)

    # If transitioning to a static_display, show content then auto-advance
    if state_type == "static_display":
        return _handle_static_display(user_id, session, state)

    return _render_state(user_id, next_state)


def _render_state(user_id, state_id: str) -> HarnessResult:
    """Render a state's template and build its inline keyboard if applicable."""
    state = sm.get_state(state_id)
    state_type = state.get("type", "")
    clinic = sm.load_clinic_info()

    # Render the template
    template_name = state.get("template", "")
    if template_name:
        rendered = tm.render(template_name, **clinic)
    else:
        rendered = ""

    # Build keyboard for states that have options
    keyboard = None

    if state_type in ("menu", "triage"):
        options = state.get("options", [])
        if options:
            keyboard = [[{"label": o["label"], "data": o["data"]}] for o in options]

    elif state_type == "doctor_select":
        result = _render_doctor_menu(state)
        return result

    elif state_type == "confirmation":
        # This shouldn't normally be called directly — _render_confirmation handles it
        keyboard = [
            [{"label": "✅ Confirm", "data": "confirm"}],
            [{"label": "🔄 Change", "data": "deny"}],
        ]

    elif state_type == "static_display":
        if state.get("show_return"):
            keyboard = [
                [{"label": "🏠 Return to Main Menu", "data": "home"}],
                [{"label": "👋 End Session", "data": "end"}],
            ]
            return HarnessResult(text=rendered, keyboard=keyboard)
        if state.get("end_session"):
            return HarnessResult(text=rendered, end_session=True)

    elif state_type == "handoff":
        return HarnessResult(text=rendered, end_session=True)

    end = state.get("end_session", False)
    return HarnessResult(text=rendered, keyboard=keyboard, end_session=end)


def _render_fallback(user_id, session) -> HarnessResult:
    """Render the fallback message and return to the current state."""
    rendered = tm.render("intent_not_understood")
    current = _render_state(user_id, session.state)
    return HarnessResult(
        text=rendered + "\n\n" + current.text,
        keyboard=current.keyboard,
    )


# ── Dummy appointment lookup ─────────────────────────────────────────

def _fetch_appointment(form_data: dict) -> dict | None:
    """Dummy: look up an appointment by reference.

    In a real system this would query Google Calendar or a database.
    For now, return a dummy appointment if any identifier is provided.
    """
    identifier = form_data.get("modify_target") or form_data.get("cancel_target")
    if identifier and len(identifier) > 0:
        # Return a dummy appointment
        return {
            "appt_date": "2026-09-10",
            "appt_time": "10:00",
            "doctor_name": "Dr. Tan Wei Ming",
        }
    return None