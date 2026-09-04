"""Per-user conversation session manager.

Maintains a rolling history of messages for each user AND tracks the
user's current state in the dialogue state machine, along with any
form data collected during multi-step flows.
"""

from collections import defaultdict
from typing import Any

# Role constants matching OpenAI / Anthropic message formats
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# Maximum number of message pairs kept per session to bound memory usage
MAX_HISTORY_MESSAGES = 20

# Fallback retry limit before routing to live agent
MAX_RETRIES = 3


class UserSession:
    """Tracks a single user's state, form data, and retry count."""

    def __init__(self) -> None:
        self.state: str = ""  # Current state machine state ID
        self.form_data: dict[str, Any] = {}  # Collected form variables
        self.form_step: int = 0  # Current step in a form_collection state
        self.retry_count: int = 0  # Consecutive unrecognised inputs
        self.history: list[dict[str, str]] = []  # Conversation history
        self.selected_doctor_id: str | None = None  # Doctor chosen by user
        self.selected_date: str | None = None  # Date selected by user
        self.selected_time: str | None = None  # Time selected by user

    def reset_flow(self) -> None:
        """Clear form data and step counter (keep conversation history)."""
        self.form_data = {}
        self.form_step = 0
        self.retry_count = 0

    def reset_all(self) -> None:
        """Full reset — state, form data, and history."""
        self.state = ""
        self.form_data = {}
        self.form_step = 0
        self.retry_count = 0
        self.selected_doctor_id = None
        self.selected_date = None
        self.selected_time = None
        self.history = []


class SessionManager:
    """In-memory store of user sessions keyed by user ID.

    For production, swap the internal dict for Redis or a database — the
    public API stays the same.
    """

    def __init__(self) -> None:
        self._sessions: dict[int | str, UserSession] = {}

    def get_session(self, user_id: int | str) -> UserSession:
        """Return the UserSession for *user_id*, creating one if needed."""
        if user_id not in self._sessions:
            self._sessions[user_id] = UserSession()
        return self._sessions[user_id]

    def get_history(self, user_id: int | str) -> list[dict[str, str]]:
        """Return the message list for *user_id* (may be empty)."""
        return list(self.get_session(user_id).history)

    def add_message(self, user_id: int | str, role: str, content: str) -> None:
        """Append a message to the user's session, trimming to last N."""
        session = self.get_session(user_id)
        session.history.append({"role": role, "content": content})
        if len(session.history) > MAX_HISTORY_MESSAGES:
            session.history = session.history[-MAX_HISTORY_MESSAGES:]

    def add_user_message(self, user_id: int | str, content: str) -> None:
        self.add_message(user_id, ROLE_USER, content)

    def add_assistant_message(self, user_id: int | str, content: str) -> None:
        self.add_message(user_id, ROLE_ASSISTANT, content)

    def clear(self, user_id: int | str) -> None:
        """Remove all stored data for a user."""
        self._sessions.pop(user_id, None)

    def all_sessions(self) -> dict[int | str, UserSession]:
        """Return a snapshot of all active sessions (useful for debugging)."""
        return dict(self._sessions)