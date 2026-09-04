from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowHygieneTests(unittest.TestCase):
    def test_ci_cancels_superseded_runs_and_does_not_persist_credentials(self):
        workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("group: oathcast-ci-${{ github.ref }}", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertGreaterEqual(workflow.count("persist-credentials: false"), 2)

    def test_read_only_workflows_do_not_persist_checkout_credentials(self):
        for name in ("oathcast-canary.yml", "provider-evidence-freshness.yml"):
            with self.subTest(workflow=name):
                workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
                self.assertIn("persist-credentials: false", workflow)

    def test_collector_uses_full_history_and_masks_its_api_key(self):
        workflow = (WORKFLOWS / "collect-provider-pairs.yml").read_text(
            encoding="utf-8"
        )

        checkout = workflow.split("- uses: actions/checkout@v4", 1)[1].split(
            "- uses: actions/setup-python@v5", 1
        )[0]
        self.assertIn("fetch-depth: 0", checkout)
        self.assertNotIn("persist-credentials: false", checkout)
        self.assertIn('echo "::add-mask::$WEATHERAPI_KEY"', workflow)
        self.assertIn("from urllib.parse import quote, quote_plus", workflow)
        self.assertIn('quote(key, safe="")', workflow)
        self.assertIn('quote_plus(key, safe="")', workflow)
        self.assertIn('variant.replace("%", "%25")', workflow)
        self.assertLess(
            workflow.index("from urllib.parse import quote, quote_plus"),
            workflow.index("PYTHONPATH=src python3 scripts/collect_provider_pairs.py"),
        )

    def test_docker_context_excludes_python_build_metadata(self):
        patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("*.egg-info", patterns)

    def test_freshness_docs_state_the_combined_default(self):
        documentation = (ROOT / "docs" / "provider-evidence-freshness.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("defaults to `all`", documentation)
        self.assertIn("both collection", documentation)


if __name__ == "__main__":
    unittest.main()
