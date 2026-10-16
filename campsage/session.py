"""In-memory, expiring sessions for the local prototype."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    student_id: str
    encryption_key: bytes
    api_key: str | None
    created_at: float
    last_seen: float
    # Short server-side memory of this conversation, used for follow-up questions.
    history: list = field(default_factory=list)
    # Consecutive turns that were not about college; reset by any campus answer.
    drift: int = 0

    def remember(self, question: str, answer: str, status: str, keep: int = 12) -> None:
        self.history.append({"role": "student", "text": question[:600]})
        self.history.append({"role": "campsage", "text": answer[:600]})
        del self.history[:-keep]
        self.drift = self.drift + 1 if status in {"general", "offtopic"} else 0


class SessionStore:
    def __init__(self, ttl_seconds: int = 8 * 60 * 60, idle_seconds: int = 60 * 60):
        self.ttl_seconds = ttl_seconds
        self.idle_seconds = idle_seconds
        self._sessions: dict[str, Session] = {}

    def create(self, student_id: str, encryption_key: bytes, api_key: str | None = None) -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        self._sessions[session_id] = Session(student_id, encryption_key, api_key, now, now)
        return session_id

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if not session:
            return None
        now = time.time()
        if now - session.created_at > self.ttl_seconds or now - session.last_seen > self.idle_seconds:
            self.destroy(session_id)
            return None
        session.last_seen = now
        return session

    def destroy(self, session_id: str | None) -> None:
        if session_id and session_id in self._sessions:
            session = self._sessions.pop(session_id)
            session.api_key = None
            session.history.clear()
            session.encryption_key = b""
