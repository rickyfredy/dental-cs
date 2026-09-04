"""Google Calendar integration service.

Handles OAuth authentication, availability lookups, and appointment
insertion for Bright Smile Dental Clinic. All events use the clinic's
timezone (Asia/Singapore by default).
"""

import logging
import os.path
from datetime import datetime, timedelta
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from config import settings

logger = logging.getLogger(__name__)

# Calendar API scope — read/write access to the clinic calendar
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Path to OAuth client secrets downloaded from Google Cloud Console
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def _get_credentials() -> Credentials:
    """Load stored OAuth token or run the interactive OAuth flow on first run."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"{CREDENTIALS_FILE} not found. Download OAuth client "
                    "secrets from Google Cloud Console and place it here."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds


def _get_service() -> Resource:
    """Build and return an authenticated Calendar API service object."""
    return build("calendar", "v3", credentials=_get_credentials())


def check_availability(date_str: str, calendar_id: str | None = None) -> dict[str, Any]:
    """Query the clinic calendar for busy blocks on *date_str* (YYYY-MM-DD).

    Args:
        date_str: Date in YYYY-MM-DD format.
        calendar_id: Google Calendar ID to query. If None, uses the default
                    from settings (GOOGLE_CALENDAR_ID).

    Returns a dict with:
        - ``date``: the queried date
        - ``busy``: list of {start, end} ISO strings for existing events
        - ``free_slots``: list of {start, end} ISO strings for open windows
    """
    service = _get_service()
    cal_id = calendar_id or settings.google_calendar_id
    tz = settings.clinic_timezone
    open_h = settings.clinic_open_hour
    close_h = settings.clinic_close_hour
    duration = settings.appointment_duration_minutes

    from zoneinfo import ZoneInfo

    tzinfo = ZoneInfo(tz)
    day_start = datetime.fromisoformat(date_str).replace(
        hour=open_h, minute=0, second=0, tzinfo=tzinfo
    )
    day_end = day_start.replace(hour=close_h, minute=0, second=0)

    # Query Google Calendar for existing events in the range
    events_result = (
        service.events()
        .list(
            calendarId=cal_id,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    busy_blocks: list[dict[str, str]] = []
    for event in events_result.get("items", []):
        start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date"))
        end = event.get("end", {}).get("dateTime", event.get("end", {}).get("date"))
        if start and end:
            busy_blocks.append({"start": start, "end": end})

    # Compute free slots by subtracting busy blocks from the working day
    free_slots = _compute_free_slots(day_start, day_end, busy_blocks, duration)

    return {
        "date": date_str,
        "busy": busy_blocks,
        "free_slots": free_slots,
    }


def _compute_free_slots(
    day_start: datetime,
    day_end: datetime,
    busy_blocks: list[dict[str, str]],
    duration_minutes: int,
) -> list[dict[str, str]]:
    """Subtract busy blocks from the working day and return free windows."""
    busy_periods: list[tuple[datetime, datetime]] = []
    for block in busy_blocks:
        b_start = datetime.fromisoformat(block["start"])
        b_end = datetime.fromisoformat(block["end"])
        busy_periods.append((b_start, b_end))

    busy_periods.sort(key=lambda p: p[0])

    slot_duration = timedelta(minutes=duration_minutes)
    free_slots: list[dict[str, str]] = []
    cursor = day_start

    for b_start, b_end in busy_periods:
        # If there's room before this busy block, add free slots
        while cursor + slot_duration <= b_start:
            slot_end = cursor + slot_duration
            free_slots.append({"start": cursor.isoformat(), "end": slot_end.isoformat()})
            cursor = slot_end
        # Move cursor past the busy block
        cursor = max(cursor, b_end)

    # Remaining time after the last busy block
    while cursor + slot_duration <= day_end:
        slot_end = cursor + slot_duration
        free_slots.append({"start": cursor.isoformat(), "end": slot_end.isoformat()})
        cursor = slot_end

    return free_slots


def book_appointment(date_str: str, time_str: str, patient_name: str, calendar_id: str | None = None) -> dict[str, Any]:
    """Insert a new appointment event on the clinic calendar.

    Args:
        date_str: Date in YYYY-MM-DD format.
        time_str: Start time in HH:MM format (24h).
        patient_name: Name of the patient for the event title.
        calendar_id: Google Calendar ID to book into. If None, uses the
                     default from settings (GOOGLE_CALENDAR_ID).

    Returns the created event dict from the Google Calendar API.
    """
    service = _get_service()
    cal_id = calendar_id or settings.google_calendar_id
    tz = settings.clinic_timezone
    duration = settings.appointment_duration_minutes

    from zoneinfo import ZoneInfo

    tzinfo = ZoneInfo(tz)
    start_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=tzinfo)
    end_dt = start_dt + timedelta(minutes=duration)

    event_body = {
        "summary": f"Dental Appointment - {patient_name}",
        "description": f"Booked via AI receptionist for {patient_name}",
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": tz,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": tz,
        },
    }

    created = (
        service.events()
        .insert(calendarId=cal_id, body=event_body)
        .execute()
    )

    logger.info("Booked appointment for %s on %s at %s — event id: %s",
                patient_name, date_str, time_str, created.get("id"))
    return created