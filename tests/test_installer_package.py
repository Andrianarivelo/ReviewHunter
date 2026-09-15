"""Check the actual downloadable archives without application dependencies.

CI sets RH_RELEASE_ARCHIVE and RH_EXTENSION_ARCHIVE after ``git archive``.
Locally the suite builds equivalent temporary archives from HEAD, so run it after
committing packaging changes. No installer, browser, or user library is opened.
"""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
# Keep the list focused on the user-facing PDF import and browser installation path.
REQUIRED_FILES = (
    "Install-ReviewHunter.bat", "install.sh", "run.py", "requirements.txt",
    "installer/reviewhunter_installer.py", "doi_hunter_web/pdf_ingest.py",
    "doi_hunter_web/fulltext.py", "doi_hunter_web/connector.py",
    "doi_hunter_web/catalog.py", "doi_hunter_web/metadata.py",
    "browser_extension/manifest.json", "browser_extension/src/core/pdf.js",
    "browser_extension/src/core/save.js", "browser_extension/src/core/handoff.js",
    "browser_extension/src/core/shadow.js", "browser_extension/src/shared/identifiers.js",
)


class InstallerPackageTests(unittest.TestCase):
    """Verify release contents and the separately installable extension zip."""

    @classmethod
    def setUpClass(cls):
        """Use CI artifacts or build matching temporary archives from committed files."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="reviewhunter-package-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.archives = {}
        for name, variable, tree, prefix in (
            ("app", "RH_RELEASE_ARCHIVE", "HEAD", "ReviewHunter/"),
            ("extension", "RH_EXTENSION_ARCHIVE", "HEAD:browser_extension", ""),
        ):
            archive_path = os.environ.get(variable)
            if not archive_path:
                archive_path = str(Path(cls.temporary.name) / f"{name}.zip")
                subprocess.run(
                    ["git", "archive", "--format=zip", f"--prefix={prefix}",
                     f"--output={archive_path}", tree],
                    cwd=ROOT, check=True, capture_output=True,
                )
            archive = zipfile.ZipFile(archive_path)
            cls.addClassCleanup(archive.close)
            cls.archives[name] = archive

    def test_release_contains_current_pdf_import_and_installer(self):
        """A successful build must ship the current code, not an older release payload."""
        for relative in REQUIRED_FILES:
            with self.subTest(file=relative):
                packaged = self.archives["app"].read("ReviewHunter/" + relative)
                # Git attributes deliberately normalize launcher line endings.
                self.assertEqual(packaged.replace(b"\r\n", b"\n"),
                                 (ROOT / relative).read_bytes().replace(b"\r\n", b"\n"))

    def test_pdf_runtime_dependencies_are_in_the_archive(self):
        """Both font decoding and browser-compatible downloads install by default."""
        requirements = self.archives["app"].read("ReviewHunter/requirements.txt").decode("utf-8")
        names = {
            re.split(r"[<>=!~\s\[]", line.strip().lower())[0].replace("-", "_")
            for line in requirements.splitlines() if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertTrue({"pypdf", "fonttools", "curl_cffi"}.issubset(names))

    def test_extension_zip_matches_bundled_extension(self):
        """The standalone Chrome download and app installer deliver identical resources."""
        extension = self.archives["extension"]
        app = self.archives["app"]
        standalone = {name for name in extension.namelist() if not name.endswith("/")}
        bundled = {
            name.removeprefix("ReviewHunter/browser_extension/")
            for name in app.namelist()
            if name.startswith("ReviewHunter/browser_extension/") and not name.endswith("/")
        }
        self.assertEqual(standalone, bundled)
        for relative in standalone:
            with self.subTest(file=relative):
                self.assertEqual(extension.read(relative), app.read("ReviewHunter/browser_extension/" + relative))

    def test_manifest_runtime_resources_exist(self):
        """Chrome can load every declared icon, entry point and web-accessible resource."""
        extension = self.archives["extension"]
        names = set(extension.namelist())
        manifest = json.loads(extension.read("manifest.json"))
        current_manifest = json.loads((ROOT / "browser_extension/manifest.json").read_text("utf-8"))
        self.assertEqual(manifest, current_manifest)
        self.assertEqual(manifest["manifest_version"], 3)
        required = list(manifest["icons"].values())
        required.extend(manifest["action"]["default_icon"].values())
        required.extend([manifest["action"]["default_popup"],
                         manifest["background"]["service_worker"], manifest["options_ui"]["page"]])
        for script in manifest.get("content_scripts", []):
            required.extend(script.get("js", []))
            required.extend(script.get("css", []))
        for entry in manifest.get("web_accessible_resources", []):
            required.extend(entry["resources"])
        for relative in required:
            with self.subTest(resource=relative):
                self.assertTrue(any(fnmatch.fnmatchcase(name, relative) for name in names), relative)
        self.assertIn("declarativeNetRequestWithHostAccess", manifest["permissions"])

    def test_launchers_survive_archive_line_endings(self):
        """The Unix launcher remains executable text and the Windows launcher uses CRLF."""
        app = self.archives["app"]
        unix = app.read("ReviewHunter/install.sh")
        windows = app.read("ReviewHunter/Install-ReviewHunter.bat")
        self.assertTrue(unix.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r\n", unix)
        self.assertIn(b"\r\n", windows)


if __name__ == "__main__":
    unittest.main()
