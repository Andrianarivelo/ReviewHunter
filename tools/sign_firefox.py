"""Have Mozilla sign the Firefox connector, so release Firefox installs it permanently with one click.

Firefox (release builds) only installs add-ons signed by Mozilla. Self-distributed ("unlisted")
add-ons are signed automatically, for free, by addons.mozilla.org (AMO); the add-on is not published
in the store. This needs an AMO API key (one time, free):
    https://addons.mozilla.org/developers/addon/api/key/  ->  "JWT issuer" and "JWT secret"

    set AMO_JWT_ISSUER=user:12345:678   set AMO_JWT_SECRET=...      (or GitHub secrets for CI)
    python tools/sign_firefox.py dist/connectors/ReviewHunter-Firefox.xpi
        -> dist/connectors/ReviewHunter-Firefox-signed.xpi

Every upload needs a new version number (browser_extension/manifest.json "version").
Uses the AMO API v5 (upload, then create the add-on or a new version, then wait for signing), with a
JWT built here (HS256), so nothing beyond the standard library and ``requests`` is needed.
NOT YET RUN against AMO (no API key during development): the first run is the real test.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path

API = os.environ.get("AMO_API", "https://addons.mozilla.org/api/v5")
TIMEOUT_S = 900


def jwt_token(issuer: str, secret: str) -> str:
    """AMO API authentication: HS256 JWT valid 5 minutes, new ``jti`` for every request."""
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"iss": issuer, "jti": uuid.uuid4().hex, "iat": now, "exp": now + 300}).encode())
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(signature)}"


def is_signed(xpi: Path) -> bool:
    """Mozilla-signed XPIs carry META-INF/mozilla.rsa (or cose.sig)."""
    with zipfile.ZipFile(xpi) as z:
        names = set(z.namelist())
    return "META-INF/mozilla.rsa" in names or "META-INF/cose.sig" in names


def sign(xpi: Path, issuer: str, secret: str, out: Path) -> Path:
    import requests

    def auth() -> dict:
        return {"Authorization": f"JWT {jwt_token(issuer, secret)}"}

    with zipfile.ZipFile(xpi) as z:
        manifest = json.loads(z.read("manifest.json"))
    guid = manifest["browser_specific_settings"]["gecko"]["id"]
    version = manifest["version"]

    print(f"uploading {xpi.name} ({guid} {version}) to AMO (unlisted)...")
    with open(xpi, "rb") as f:
        r = requests.post(f"{API}/addons/upload/", headers=auth(), files={"upload": (xpi.name, f)},
                          data={"channel": "unlisted"}, timeout=120)
    r.raise_for_status()
    upload = r.json()
    deadline = time.time() + TIMEOUT_S
    while not upload.get("processed"):
        if time.time() > deadline:
            raise TimeoutError("AMO did not process the upload in time")
        time.sleep(5)
        upload = requests.get(f"{API}/addons/upload/{upload['uuid']}/", headers=auth(), timeout=60).json()
    if not upload.get("valid"):
        raise RuntimeError("AMO rejected the add-on: " + json.dumps(upload.get("validation", {}).get("messages", []))[:3000])

    exists = requests.get(f"{API}/addons/addon/{guid}/", headers=auth(), timeout=60).status_code == 200
    if exists:
        r = requests.post(f"{API}/addons/addon/{guid}/versions/", headers=auth(), json={"upload": upload["uuid"]},
                          timeout=60)
    else:
        r = requests.post(f"{API}/addons/addon/", headers=auth(), json={"version": {"upload": upload["uuid"]}},
                          timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"AMO refused the version ({r.status_code}): {r.text[:2000]}")
    version_id = (r.json().get("current_version") or r.json()).get("id") if not exists else r.json()["id"]

    print("waiting for Mozilla's signature...")
    while True:
        if time.time() > deadline:
            raise TimeoutError("the signed file did not become available in time")
        v = requests.get(f"{API}/addons/addon/{guid}/versions/{version_id}/", headers=auth(), timeout=60).json()
        file = v.get("file") or {}
        if file.get("status") == "public" and file.get("url"):
            break
        if file.get("status") in ("disabled", "rejected"):
            raise RuntimeError(f"signing failed: {file.get('status')}")
        time.sleep(10)
    data = requests.get(file["url"], headers=auth(), timeout=120)
    data.raise_for_status()
    out.write_bytes(data.content)
    if not is_signed(out):
        raise RuntimeError(f"downloaded {out} but it carries no Mozilla signature")
    print(f"signed: {out}")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    xpi = Path(argv[0])
    issuer, secret = os.environ.get("AMO_JWT_ISSUER", ""), os.environ.get("AMO_JWT_SECRET", "")
    if not (issuer and secret):
        print("AMO_JWT_ISSUER / AMO_JWT_SECRET are not set: see the help above.", file=sys.stderr)
        return 2
    sign(xpi, issuer, secret, xpi.with_name(xpi.stem + "-signed.xpi"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
