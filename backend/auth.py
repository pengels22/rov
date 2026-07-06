import hmac
import secrets
import threading
import time
from http.cookies import SimpleCookie
from typing import Dict, Optional


class SessionAuth:
    COOKIE_NAME = "rov_session"

    def __init__(self, username: str, password: str, session_hours: float = 12):
        self.username = username
        self.password = password
        self.session_seconds = max(60.0, float(session_hours) * 3600.0)
        self._lock = threading.Lock()
        self._sessions: Dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    def login(self, username: str, password: str) -> Optional[str]:
        if not self.configured:
            return None
        if not (
            hmac.compare_digest(str(username), self.username)
            and hmac.compare_digest(str(password), self.password)
        ):
            return None
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = time.monotonic() + self.session_seconds
        return session_id

    def authenticated(self, cookie_header: str) -> bool:
        session_id = self._session_from_cookie(cookie_header)
        if not session_id:
            return False
        now = time.monotonic()
        with self._lock:
            expires_at = self._sessions.get(session_id)
            if expires_at is None or expires_at <= now:
                self._sessions.pop(session_id, None)
                return False
            self._sessions[session_id] = now + self.session_seconds
        return True

    def logout(self, cookie_header: str) -> None:
        session_id = self._session_from_cookie(cookie_header)
        if session_id:
            with self._lock:
                self._sessions.pop(session_id, None)

    def session_cookie(self, session_id: str) -> str:
        max_age = int(self.session_seconds)
        return (
            f"{self.COOKIE_NAME}={session_id}; Path=/; HttpOnly; "
            f"SameSite=Strict; Max-Age={max_age}"
        )

    def clear_cookie(self) -> str:
        return (
            f"{self.COOKIE_NAME}=; Path=/; HttpOnly; "
            "SameSite=Strict; Max-Age=0"
        )

    def _session_from_cookie(self, cookie_header: str) -> Optional[str]:
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_header or "")
            morsel = cookie.get(self.COOKIE_NAME)
            return morsel.value if morsel else None
        except Exception:
            return None
