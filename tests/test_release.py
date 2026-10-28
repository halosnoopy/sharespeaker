"""Public archive boundary and privacy regressions; no hardware is touched."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts/build_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_real_public_files_pass_audit(self):
        self.assertTrue(release.public_payload())

    def test_private_files_cannot_enter_archive_by_directory_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "release-files.txt").write_text("README.md\n", encoding="utf-8")
            (root / "README.md").write_text("Public project", encoding="utf-8")
            for private in (".local", ".git", "backups"):
                (root / private).mkdir()
                (root / private / "private.txt").write_text("private", encoding="utf-8")
            archive, count = release.build(root)
            self.assertEqual(1, count)
            with zipfile.ZipFile(archive) as output:
                self.assertEqual(["sharespeaker/README.md"], output.namelist())

    def test_unsafe_manifest_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("../private.txt", ".git/config", ".local/settings.json", "dist/old.zip", "/private", "C:/private", "file.log", ".env"):
                (root / "release-files.txt").write_text(name + "\n", encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(ValueError):
                    release.release_paths(root)

    def test_audit_accepts_synthetic_addresses_and_rejects_realistic_ones(self):
        self.assertEqual([], release.audit_text("example", "02:00:00:00:00:10 AA:BB:CC:DD:EE:01"))
        # Construct a non-synthetic address without embedding equipment identity.
        address = ":".join(["10", "20", "30", "40", "50", "60"])
        self.assertTrue(release.audit_text("example", address))
        self.assertTrue(release.audit_text("example", address.replace(":", "_")))

    def test_findings_do_not_echo_private_values(self):
        private = "/" + "home/" + "private-person/file"
        findings = release.audit_text("example", private)
        self.assertTrue(findings)
        self.assertNotIn(private, "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
