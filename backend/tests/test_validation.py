import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validation import require_bool


class ValidationTests(unittest.TestCase):
    def test_boolean_false_remains_false(self):
        self.assertIs(require_bool({"enabled": False}, "enabled"), False)

    def test_string_false_is_rejected(self):
        with self.assertRaises(ValueError):
            require_bool({"enabled": "false"}, "enabled")

    def test_missing_boolean_is_rejected(self):
        with self.assertRaises(ValueError):
            require_bool({}, "enabled")


if __name__ == "__main__":
    unittest.main()
