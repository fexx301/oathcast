import json
from pathlib import Path
import subprocess
import sys
import unittest

from oathcast.presentation import render_application_demo_markdown


ROOT = Path(__file__).resolve().parents[1]


class PresentationTests(unittest.TestCase):
    def test_demo_markdown_keeps_fixture_and_protocol_boundaries_visible(self):
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "demo_application.py"),
                "--compare-owned-fallback",
            ],
            cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src")},
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(process.stdout)
        rendered = render_application_demo_markdown(payload)
        self.assertIn("DEVELOPMENT FIXTURE ONLY", rendered)
        self.assertIn("independent-weather-alpha", rendered)
        self.assertIn("External influence detected:** yes", rendered)
        self.assertIn("Ablation passed:** yes", rendered)
        self.assertIn("not present in this fixture run", rendered)
        self.assertIn("does not prove Miner registration", rendered)

    def test_external_miner_content_is_escaped_for_markdown(self):
        payload = {
            "decision": {
                "event_id": "event-1",
                "replies": [
                    {
                        "slug": "external",
                        "content": "<script>alert(1)</script> [pay](javascript:alert(1)) **bold**",
                        "raw_response": {},
                    }
                ],
            },
            "resolution": {},
            "case_evidence": {"question": {}, "decisions": [], "resolutions": []},
        }

        rendered = render_application_demo_markdown(payload)

        self.assertIn("&lt;script&gt;alert", rendered)
        self.assertIn(r"\[pay\]\(javascript:alert\(1\)\)", rendered)
        self.assertIn(r"\*\*bold\*\*", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("[pay](javascript:alert(1))", rendered)


if __name__ == "__main__":
    unittest.main()
