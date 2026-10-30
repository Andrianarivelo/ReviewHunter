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


SAFARI_APP_ID = "org.reviewhunter.connector"   # the extension inside is SAFARI_APP_ID + ".Extension"


def align_xcode_bundle_ids(pbxproj: Path, app_id: str = SAFARI_APP_ID) -> dict:
    """Give the wrapper app the bundle ID its extension is prefixed with.

    safari-web-extension-converter names the extension "<--bundle-identifier>.Extension" but derives the app's
    ID from the app name (org.reviewhunter.ReviewHunter-Connector), and Xcode refuses to embed an extension
    whose ID does not start with the app's ("Embedded binary's bundle identifier is not prefixed...").
    """
    import re

    text = pbxproj.read_text(encoding="utf-8")
    pattern = re.compile(r'(PRODUCT_BUNDLE_IDENTIFIER = )"?([^";]+)"?;')
    before = sorted({m.group(2) for m in pattern.finditer(text)})
    text = pattern.sub(lambda m: m.group(0) if m.group(2).endswith(".Extension") else f"{m.group(1)}{app_id};", text)
    pbxproj.write_text(text, encoding="utf-8")
    after = sorted({m.group(2) for m in pattern.finditer(text)})
    ok = all(i == app_id or i.startswith(app_id + ".") for i in after)
    return {"before": before, "after": after, "ok": ok}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "dist" / "connectors")
    p.add_argument("--align-xcode", type=Path, metavar="PROJECT.pbxproj",
                   help="after safari-web-extension-converter: align the app and extension bundle IDs")
    args = p.parse_args(argv)
    if args.align_xcode:
        result = align_xcode_bundle_ids(args.align_xcode)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    print(json.dumps(build(args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
