import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import SessionAuth


class SessionAuthTests(unittest.TestCase):
    def test_login_creates_authenticated_cookie(self):
        auth = SessionAuth("operator", "secret")
        session_id = auth.login("operator", "secret")
        self.assertIsNotNone(session_id)
        self.assertTrue(auth.authenticated(f"rov_session={session_id}"))

    def test_wrong_credentials_are_rejected(self):
        auth = SessionAuth("operator", "secret")
        self.assertIsNone(auth.login("operator", "wrong"))
        self.assertIsNone(auth.login("wrong", "secret"))

    def test_logout_invalidates_session(self):
        auth = SessionAuth("operator", "secret")
        session_id = auth.login("operator", "secret")
        cookie = f"rov_session={session_id}"
        auth.logout(cookie)
        self.assertFalse(auth.authenticated(cookie))

    def test_incomplete_configuration_is_disabled(self):
        self.assertFalse(SessionAuth("operator", "").configured)


if __name__ == "__main__":
    unittest.main()
