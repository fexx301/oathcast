from pathlib import Path
import tempfile
import unittest

from oathcast.artifacts import atomic_write_text


class AtomicArtifactTests(unittest.TestCase):
    def test_atomic_write_replaces_existing_content_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text("old\n", encoding="utf-8")
            path.chmod(0o640)

            atomic_write_text(path, "new\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_validation_failure_preserves_previous_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text("old\n", encoding="utf-8")

            def reject(candidate: Path) -> None:
                self.assertEqual(candidate.read_text(encoding="utf-8"), "new\n")
                raise ValueError("invalid artifact")

            with self.assertRaisesRegex(ValueError, "invalid artifact"):
                atomic_write_text(path, "new\n", validate=reject)

            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
