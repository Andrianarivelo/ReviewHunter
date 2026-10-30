"""Build the Firefox and Safari connectors from the one connector source (browser_extension/).

    dist/connectors/firefox/                      unpacked Firefox add-on
    dist/connectors/ReviewHunter-Firefox.xpi      the same, zipped (unsigned: see tools/sign_firefox.py)
    dist/connectors/safari/                       web extension for Apple's converter (macOS + Xcode):
        xcrun safari-web-extension-converter dist/connectors/safari --macos-only --no-open --no-prompt \\
              --app-name "ReviewHunter Connector" --bundle-identifier org.reviewhunter.connector
    (the CI job "safari" runs the converter and xcodebuild and publishes the app)

Only manifest.json differs between browsers; the code is shared (every module uses
``globalThis.browser ?? globalThis.chrome``):
    Firefox  background "scripts" (Firefox has no extension service workers), add-on ID (native messaging
             and storage need it), minimum version 140 (current ESR; Mozilla data-collection declaration), no data collection
    Safari   no nativeMessaging (Safari's goes to the wrapper app: the connector uses reviewhunter:// links)

Usage:  python tools/build_extensions.py [--out dist/connectors]
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "browser_extension"
GECKO_ID = "connector@reviewhunter.app"          # must match doi_hunter_web.native_host.GECKO_ID
FIREFOX_MIN = "140.0"                              # data_collection_permissions; current ESR
SAFARI_MIN = "17.0"
RUNTIME = ("manifest.json", "icons", "src")       # tests, README and fixtures are not shipped
EXCLUDE = {"__pycache__", ".DS_Store"}


def firefox_manifest(m: dict) -> dict:
    m = copy.deepcopy(m)
    m.pop("minimum_chrome_version", None)
    m.pop("key", None)
    bg = m.get("background", {})
    m["background"] = {"scripts": [bg.get("service_worker", "src/background.js")], "type": bg.get("type", "module")}
    m["browser_specific_settings"] = {"gecko": {
        "id": GECKO_ID, "strict_min_version": FIREFOX_MIN,
        # Mozilla's required declaration (2025): the connector only talks to ReviewHunter on this computer.
        "data_collection_permissions": {"required": ["none"]},
    }}
    return m


def safari_manifest(m: dict) -> dict:
    m = copy.deepcopy(m)
    m.pop("minimum_chrome_version", None)
    m.pop("key", None)
    m["permissions"] = [p for p in m.get("permissions", []) if p != "nativeMessaging"]
    m["browser_specific_settings"] = {"safari": {"strict_min_version": SAFARI_MIN}}
    return m


def _copy_runtime(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in RUNTIME:
        src = SOURCE / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns(*EXCLUDE))
        else:
            shutil.copy2(src, dest / name)


def build(out: Path) -> dict:
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    results = {}
    for browser, transform in (("firefox", firefox_manifest), ("safari", safari_manifest)):
        dest = out / browser
        _copy_runtime(dest)
        (dest / "manifest.json").write_text(json.dumps(transform(source_manifest), indent=2) + "\n", encoding="utf-8")
        results[browser] = str(dest)
    xpi = out / "ReviewHunter-Firefox.xpi"
    xpi.unlink(missing_ok=True)
    with zipfile.ZipFile(xpi, "w", zipfile.ZIP_DEFLATED) as z:   # manifest.json at the archive root
        for f in sorted((out / "firefox").rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(out / "firefox").as_posix())
    results["xpi"] = str(xpi)
    results["version"] = source_manifest["version"]
    return results


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "dist" / "connectors")
    args = p.parse_args(argv)
    print(json.dumps(build(args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
