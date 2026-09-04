"""Email confirmation service.

Sends appointment confirmation emails to patients via SMTP after a
booking is created or rescheduled.  Email body text is loaded from
template files in ``templates/`` so developers can customise the
wording without touching code.

Uses Python's built-in ``smtplib`` and ``email.mime`` — no external
dependencies required.
"""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from config import settings
import template_manager as tm

logger = logging.getLogger(__name__)


def _build_message(
    to_email: str,
    subject: str,
    body: str,
) -> MIMEMultipart:
    """Construct a MIME multipart message with plain-text body."""
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def _render_email_template(template_name: str, **variables: Any) -> tuple[str, str]:
    """Render an email template and return (subject, body).

    Email templates use the same ``{variable}`` placeholder syntax as
    other templates.  The first line starting with ``Subject:`` is
    extracted as the email subject; the remaining lines form the body.
    """
    rendered = tm.render(template_name, **variables)

    lines = rendered.splitlines()
    subject = ""
    body_start = 0

    for i, line in enumerate(lines):
        if line.strip().lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
            body_start = i + 1
            # Skip a blank line after the subject line if present
            if body_start < len(lines) and lines[body_start].strip() == "":
                body_start += 1
            break

    body = "\n".join(lines[body_start:]).strip()
    return subject, body


def _send(to_email: str, subject: str, body: str) -> bool:
    """Send an email via SMTP.  Returns True on success, False on failure.

    Errors are logged but never raised — email confirmation is a
    best-effort side effect and should not break the booking flow.
    """
    if not settings.smtp_host:
        logger.warning("SMTP_HOST not configured — skipping email send to %s", to_email)
        return False

    msg = _build_message(to_email, subject, body)

    try:
        if settings.smtp_use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                server.starttls(context=context)
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, [to_email], msg.as_string())

        logger.info("Confirmation email sent to %s — subject: %s", to_email, subject)
        return True

    except Exception as exc:
        logger.error("Failed to send confirmation email to %s: %s", to_email, exc, exc_info=True)
        return False


def send_booking_confirmation(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    selected_date: str,
    selected_time: str,
    visit_reason: str,
    **clinic_info: Any,
) -> bool:
    """Send a new-booking confirmation email to the patient.

    Args:
        patient_email: Recipient email address.
        patient_name: Patient's name.
        doctor_name: Selected dentist's name (or "Any available dentist").
        selected_date: Appointment date (YYYY-MM-DD).
        selected_time: Appointment time (HH:MM).
        visit_reason: Reason for visit.
        **clinic_info: Clinic details (clinic_name, clinic_address, etc.)

    Returns True if the email was sent successfully.
    """
    subject, body = _render_email_template(
        "email_booking_confirmation",
        patient_name=patient_name,
        doctor_name=doctor_name,
        selected_date=selected_date,
        selected_time=selected_time,
        visit_reason=visit_reason,
        **clinic_info,
    )
    return _send(patient_email, subject, body)


def send_reschedule_confirmation(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    selected_date: str,
    selected_time: str,
    **clinic_info: Any,
) -> bool:
    """Send a reschedule confirmation email to the patient.

    Args:
        patient_email: Recipient email address.
        patient_name: Patient's name.
        doctor_name: Selected dentist's name (or "Any available dentist").
        selected_date: New appointment date (YYYY-MM-DD).
        selected_time: New appointment time (HH:MM).
        **clinic_info: Clinic details (clinic_name, clinic_address, etc.)

    Returns True if the email was sent successfully.
    """
    subject, body = _render_email_template(
        "email_reschedule_confirmation",
        patient_name=patient_name,
        doctor_name=doctor_name,
        selected_date=selected_date,
        selected_time=selected_time,
        **clinic_info,
    )
    return _send(patient_email, subject, body)