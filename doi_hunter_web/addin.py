"""Microsoft Word add-in plumbing: HTTPS certificate, manifest, registration status.

Office add-ins must be served over HTTPS, even from localhost. ReviewHunter therefore
runs a second listener on ``https://localhost:8766`` (see ``__main__``) with:

* a private local certificate authority "ReviewHunter Local CA" (valid 10 years),
* a ``localhost`` / ``127.0.0.1`` leaf certificate signed by that CA (valid 2 years).

Trusting the CA and registering the manifest change Windows/Office security settings,
so they are performed by ``scripts/setup_word_addin.ps1`` which the USER runs (Windows
shows its own confirmation dialog for the certificate). This module only prepares files
and reports status.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

from . import config

ADDIN_ID = "6f1f2a57-4c1b-4a0e-9d6e-7e0c5b1a9a01"   # stable GUID for the ReviewHunter add-in
CA_NAME = "ReviewHunter Local CA"
REPO_ROOT = Path(__file__).resolve().parents[1]


def paths() -> dict[str, Path]:
    d = config.certs_dir()
    addin_dir = config.library_root() / "word_addin"
    addin_dir.mkdir(parents=True, exist_ok=True)
    return {
        "ca_cert": d / "reviewhunter-ca.crt",
        "ca_key": d / "reviewhunter-ca.key",
        "cert": d / "localhost.crt",
        "key": d / "localhost.key",
        "manifest": addin_dir / "manifest.xml",
    }


def ensure_certificates(force: bool = False) -> dict[str, Path]:
    """Create CA + leaf certificate if missing or expiring within 30 days."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    p = paths()
    now = dt.datetime.now(dt.timezone.utc)

    def expiring(cert_path: Path) -> bool:
        if not cert_path.exists():
            return True
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        return cert.not_valid_after_utc - now < dt.timedelta(days=30)

    def unconstrained(cert_path: Path) -> bool:
        """CAs made before 2.11 could sign certificates for any site: replace them (see NameConstraints below)."""
        try:
            x509.load_pem_x509_certificate(cert_path.read_bytes()).extensions.get_extension_for_class(x509.NameConstraints)
            return False
        except (OSError, ValueError, x509.ExtensionNotFound):
            return True

    if force or expiring(p["ca_cert"]) or not p["ca_key"].exists() or unconstrained(p["ca_cert"]):
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
                             x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ReviewHunter (local only)")])
        ca_cert = (
            x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            # Name constraints: this CA can only vouch for localhost / 127.0.0.1 / ::1. Even if its key file
            # leaked, it could not impersonate any real website to the account that trusts it.
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("localhost"),
                                    x509.IPAddress(ipaddress.ip_network("127.0.0.0/8")),
                                    x509.IPAddress(ipaddress.ip_network("::1/128"))],
                excluded_subtrees=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        p["ca_key"].write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
        p["ca_cert"].write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        force = True  # new CA -> new leaf

    if force or expiring(p["cert"]) or not p["key"].exists():
        ca_key = serialization.load_pem_private_key(p["ca_key"].read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(p["ca_cert"].read_bytes())
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=730))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        p["key"].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        p["cert"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return p


def write_manifest() -> Path:
    port = int(config.get_settings().get("https_port") or 8766)
    base = f"https://localhost:{port}"
    manifest = f"""<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0"
           xmlns:ov="http://schemas.microsoft.com/office/taskpaneappversionoverrides"
           xsi:type="TaskPaneApp">
  <Id>{ADDIN_ID}</Id>
  <Version>2.0.0.0</Version>
  <ProviderName>ReviewHunter</ProviderName>
  <DefaultLocale>en-US</DefaultLocale>
  <DisplayName DefaultValue="ReviewHunter"/>
  <Description DefaultValue="Insert live citations and bibliographies from your local ReviewHunter library."/>
  <IconUrl DefaultValue="{base}/addin/assets/icon-32.png"/>
  <HighResolutionIconUrl DefaultValue="{base}/addin/assets/icon-80.png"/>
  <SupportUrl DefaultValue="https://github.com/Andrianarivelo/ReviewHunter"/>
  <AppDomains>
    <AppDomain>https://localhost:{port}</AppDomain>
  </AppDomains>
  <Hosts>
    <Host Name="Document"/>
  </Hosts>
  <Requirements>
    <Sets DefaultMinVersion="1.3">
      <Set Name="WordApi" MinVersion="1.3"/>
    </Sets>
  </Requirements>
  <DefaultSettings>
    <SourceLocation DefaultValue="{base}/addin/taskpane.html"/>
  </DefaultSettings>
  <Permissions>ReadWriteDocument</Permissions>
  <VersionOverrides xmlns="http://schemas.microsoft.com/office/taskpaneappversionoverrides" xsi:type="VersionOverridesV1_0">
    <Hosts>
      <Host xsi:type="Document">
        <DesktopFormFactor>
          <GetStarted>
            <Title resid="GetStarted.Title"/>
            <Description resid="GetStarted.Description"/>
            <LearnMoreUrl resid="GetStarted.LearnMoreUrl"/>
          </GetStarted>
          <FunctionFile resid="Commands.Url"/>
          <ExtensionPoint xsi:type="PrimaryCommandSurface">
            <OfficeTab id="TabReferences">
              <Group id="ReviewHunter.Group">
                <Label resid="Group.Label"/>
                <Icon>
                  <bt:Image size="16" resid="Icon.16"/>
                  <bt:Image size="32" resid="Icon.32"/>
                  <bt:Image size="80" resid="Icon.80"/>
                </Icon>
                <Control xsi:type="Button" id="ReviewHunter.Open">
                  <Label resid="Open.Label"/>
                  <Supertip>
                    <Title resid="Open.Label"/>
                    <Description resid="Open.Tooltip"/>
                  </Supertip>
                  <Icon>
                    <bt:Image size="16" resid="Icon.16"/>
                    <bt:Image size="32" resid="Icon.32"/>
                    <bt:Image size="80" resid="Icon.80"/>
                  </Icon>
                  <Action xsi:type="ShowTaskpane">
                    <TaskpaneId>ReviewHunterPane</TaskpaneId>
                    <SourceLocation resid="Taskpane.Url"/>
                  </Action>
                </Control>
              </Group>
            </OfficeTab>
          </ExtensionPoint>
        </DesktopFormFactor>
      </Host>
    </Hosts>
    <Resources>
      <bt:Images>
        <bt:Image id="Icon.16" DefaultValue="{base}/addin/assets/icon-16.png"/>
        <bt:Image id="Icon.32" DefaultValue="{base}/addin/assets/icon-32.png"/>
        <bt:Image id="Icon.80" DefaultValue="{base}/addin/assets/icon-80.png"/>
      </bt:Images>
      <bt:Urls>
        <bt:Url id="Commands.Url" DefaultValue="{base}/addin/commands.html"/>
        <bt:Url id="Taskpane.Url" DefaultValue="{base}/addin/taskpane.html"/>
        <bt:Url id="GetStarted.LearnMoreUrl" DefaultValue="https://github.com/Andrianarivelo/ReviewHunter"/>
      </bt:Urls>
      <bt:ShortStrings>
        <bt:String id="GetStarted.Title" DefaultValue="ReviewHunter is ready"/>
        <bt:String id="Group.Label" DefaultValue="ReviewHunter"/>
        <bt:String id="Open.Label" DefaultValue="ReviewHunter"/>
      </bt:ShortStrings>
      <bt:LongStrings>
        <bt:String id="GetStarted.Description" DefaultValue="Open the References tab and click ReviewHunter to cite."/>
        <bt:String id="Open.Tooltip" DefaultValue="Insert citations and a bibliography from ReviewHunter."/>
      </bt:LongStrings>
    </Resources>
  </VersionOverrides>
</OfficeApp>
"""
    target = paths()["manifest"]
    target.write_text(manifest, encoding="utf-8")
    return target


def _ca_thumbprint() -> Optional[str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    p = paths()["ca_cert"]
    if not p.exists():
        return None
    return x509.load_pem_x509_certificate(p.read_bytes()).fingerprint(hashes.SHA1()).hex().upper()


def _mac_wef() -> Path:
    """Folder Word for Mac reads sideloaded (developer) add-in manifests from."""
    return Path.home() / "Library" / "Containers" / "com.microsoft.Word" / "Data" / "Documents" / "wef"


def ca_trusted() -> bool:
    """Is our CA in the current user's Trusted Root store (Windows) or trusted by the keychain (macOS)?"""
    thumb = _ca_thumbprint()
    if not thumb:
        return False
    if sys.platform == "darwin":
        # verify-cert succeeds only when the keychain trust settings make this root valid.
        try:
            out = subprocess.run(["security", "verify-cert", "-c", str(paths()["ca_cert"])],
                                 capture_output=True, text=True, timeout=10)
            return out.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["certutil", "-user", "-store", "Root", thumb], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.returncode == 0 and thumb.lower() in out.stdout.replace(" ", "").lower()
    except (OSError, subprocess.SubprocessError):
        return False


def manifest_registered() -> bool:
    if sys.platform == "darwin":
        return (_mac_wef() / f"{ADDIN_ID}.manifest.xml").exists()
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\16.0\WEF\Developer") as k:
            value, _ = winreg.QueryValueEx(k, ADDIN_ID)
            return Path(value).exists()
    except OSError:
        return False


def status() -> dict:
    p = paths()
    return {
        "certificate": p["cert"].exists(),
        "ca_trusted": ca_trusted(),
        "manifest": str(p["manifest"]) if p["manifest"].exists() else "",
        "registered": manifest_registered(),
        "https_url": f"https://localhost:{config.get_settings().get('https_port') or 8766}/addin/taskpane.html",
        "setup_script": str(REPO_ROOT / "scripts" / "setup_word_addin.ps1"),
    }


def prepare_and_launch_setup(quiet: bool = True) -> dict:
    """Generate cert + manifest, then run the setup script.

    quiet (default, the "ReviewHunter for Word" shortcut and Settings button): hidden console, no questions;
    the only interaction is Windows' own certificate warning, then a dialog says what to do next.
    """
    p = ensure_certificates()
    manifest = write_manifest()
    script = REPO_ROOT / "scripts" / "setup_word_addin.ps1"
    launched = False
    if os.name == "nt" and script.exists():
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-CaCert", str(p["ca_cert"]), "-Manifest", str(manifest), "-AddinId", ADDIN_ID]
        if quiet:
            subprocess.Popen(args[:1] + ["-WindowStyle", "Hidden"] + args[1:] + ["-Quiet"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.Popen(args[:1] + ["-NoExit"] + args[1:], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        launched = True
    return {"ok": True, "launched": launched, **status()}


def install_mac() -> dict:
    """Word for Mac: trust the CA in the login keychain (macOS asks for the password) and sideload the manifest.

    Word for Mac reads developer manifests from ~/Library/Containers/com.microsoft.Word/Data/Documents/wef.
    NOT tested on a Mac with Word (no such machine during development).
    """
    p = ensure_certificates()
    manifest = write_manifest()
    wef = _mac_wef()
    wef.mkdir(parents=True, exist_ok=True)
    (wef / f"{ADDIN_ID}.manifest.xml").write_bytes(manifest.read_bytes())
    keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
    trust = subprocess.run(["security", "add-trusted-cert", "-r", "trustRoot", "-k", str(keychain), str(p["ca_cert"])],
                           capture_output=True, text=True, timeout=300)
    return {"ok": trust.returncode == 0, "manifest": str(wef / f"{ADDIN_ID}.manifest.xml"),
            "trusted": trust.returncode == 0, "error": trust.stderr.strip()[:300]}


def new_guid() -> str:  # helper kept for manual manifest variants
    return str(uuid.uuid4())
