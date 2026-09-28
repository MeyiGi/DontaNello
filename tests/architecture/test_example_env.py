import unittest
from pathlib import Path


class ExampleEnvironmentTests(unittest.TestCase):
    def test_example_never_contains_credentials(self):
        root = Path(__file__).resolve().parents[2]
        for line in (root / ".env.example").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if "TOKEN" in key or "API_KEY" in key or key == "TELEGRAM_CHAT_ID":
                    self.assertEqual(value.strip(), "", f"Credential-bearing example field: {key}")
