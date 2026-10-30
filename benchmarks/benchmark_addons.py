"""Benchmark N: browser connectors and Word add-in, version 2.10 (PREV_REV) vs 2.11 (this working tree).

Three measurements, all from real runs except C:
    A. Word add-in certificate: could the local CA be misused for real websites?
       For each version, its own ``ensure_certificates`` creates the CA (2.10 code taken from git). For
       every domain in DOMAINS a server certificate is forged with that CA key, served on 127.0.0.1, and a
       client that trusts the CA connects with the domain as the server name (OpenSSL chain + hostname
       check, as a browser would). Control: the genuine localhost certificate.
    B. Firefox connector in a REAL Firefox (tools/extension_firefox_e2e.py, 9 checks: add-on loads, native
       messaging start, server survives the helper, same-window open, tab reuse without reload, the PDF
       download really carries the Referer (local server records it), popup auto-start). The 2.10 connector (git PREV_REV) is packaged with the same Firefox
       manifest transform, so only the connector code differs. E2E_REPEATS runs per version. The Chromium
       suite (tools/extension_launch_e2e.py) runs once on 2.11 as a regression check.
    C. User actions needed to install each add-on, COUNTED FROM THE DOCUMENTED PROCEDURES (README,
       Settings texts) for both versions: not a timed user study.

Statistics (plot step): A and B use Fisher's exact test (2x2: version x accepted/passed). Its only
assumption is independent observations: true for A (one forged certificate per domain); for B the 9
checks of one run are not independent, so B is also reported per run (checks passed per run) and the
Fisher p is labelled descriptive.

Usage:
    .venv/Scripts/python.exe benchmarks/benchmark_addons.py measure --firefox <exe> --geckodriver <exe> [--pylib <dir>]
    %USERPROFILE%/miniconda3/python.exe benchmarks/benchmark_addons.py plot
Outputs (benchmarks/results/): N_addons.{png,pdf,svg}, N_certificate_forgery.csv, N_firefox_e2e.csv,
    N_chromium_e2e.csv, N_install_steps.csv, N_statistics.csv
"""
from __future__ import annotations

# ======================================================================================
# Parameters
# ======================================================================================
PREV_REV = "1d6e0c6"                       # 2.10.0
E2E_REPEATS = 3                            # real Firefox runs per connector version
DOMAINS = ["www.google.com", "accounts.google.com", "github.com", "login.microsoftonline.com", "www.paypal.com",
           "www.bankofamerica.com", "www.apple.com", "outlook.office.com", "www.amazon.com", "www.facebook.com",
           "www.wikipedia.org", "mail.proton.me", "www.nature.com", "www.sciencedirect.com",
           "pubmed.ncbi.nlm.nih.gov", "scholar.google.com", "www.unige.ch", "drive.google.com",
           "addons.mozilla.org", "update.microsoft.com"]
VERSIONS = {"previous": "2.10", "current": "2.11"}
FIG_DPI = 300
FONT_SIZE = 7
BAR_WIDTH = 0.3
# Short labels of the e2e checks in figure panel B (full names in N_firefox_e2e.csv).
SHORT_CHECKS = {
    "add-on loaded in Firefox": "add-on loads",
    "server stopped before the test": "server stopped (setup)",
    "startApp starts ReviewHunter (Firefox native messaging)": "starts the app",
    "the server runs this install and outlives the helper": "server outlives helper",
    "openApp opens one app tab in the same window": "opens app, same window",
    "the app shows the saved paper": "shows the saved paper",
    "second open reuses the tab and switches paper without reload": "reuses tab, no reload",
    "the PDF download carries the article page as Referer": "PDF request has Referer",
    "the popup starts ReviewHunter by itself": "popup starts the app",
}
COLORS = {"previous": "#9aa3b2", "current": "#0284c7", "fail": "#e11d48", "pass": "#10b981", "na": "#e5e7eb"}

# Documented install procedures (C). Each entry: user actions (clicks, dialog answers, typed or pasted
# paths), and whether a console window is shown. None = not possible in that version.
INSTALL_STEPS = [
    # target, version, actions, console, note
    ("Chrome / Edge / Brave", "previous", 5, False, "type chrome://extensions, Developer mode, Load unpacked, paste path, Select"),
    ("Chrome / Edge / Brave", "current", 5, False, "shortcut, Developer mode, Load unpacked, paste path (copied), Select"),
    ("Firefox (signed release)", "previous", None, False, "no Firefox build"),
    ("Firefox (signed release)", "current", 2, False, "shortcut, Add"),
    ("Firefox (no signed build)", "previous", None, False, "no Firefox build"),
    ("Firefox (no signed build)", "current", 4, False, "shortcut, Load Temporary Add-on, paste path, Open (again after each Firefox restart)"),
    ("Safari (unsigned app)", "previous", None, False, "no Safari build"),
    ("Safari (unsigned app)", "current", 7, False, "shortcut, Settings, Extensions, tick, allow websites, Advanced > web developer features, Allow Unsigned"),
    ("Word (Windows)", "previous", 7, True, "Settings, Word tab, Set up, Windows Yes, cache Y, Enter, close console"),
    ("Word (Windows)", "current", 3, False, "shortcut, Windows Yes, OK"),
    ("Word (macOS)", "previous", None, False, "Windows only"),
    ("Word (macOS)", "current", 3, False, "shortcut, password, OK (untested on a Mac)"),
]

import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"
sys.path.insert(0, str(ROOT))


# ======================================================================================
# A. Certificate forgery
# ======================================================================================

def _load_addin(version: str, home: Path):
    """The add-in certificate module of a version, with its own library folder (REVIEWHUNTER_HOME)."""
    os.environ["REVIEWHUNTER_HOME"] = str(home)
    if version == "current":
        from doi_hunter_web import addin

        return addin
    src = subprocess.run(["git", "show", f"{PREV_REV}:doi_hunter_web/addin.py"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.replace("from . import config", "from doi_hunter_web import config")
    path = home / "addin_prev.py"
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("addin_prev", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serve_once(cert_file: str, key_file: str) -> int:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        try:
            conn, _ = srv.accept()
            with ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(1)
        except (OSError, ssl.SSLError):
            pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def _client_accepts(port: int, ca_file: Path, name: str) -> tuple[bool, str]:
    ctx = ssl.create_default_context(cafile=str(ca_file))      # chain AND hostname checks, like a browser
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=name) as s:
                s.sendall(b"x")
        return True, ""
    except ssl.SSLCertVerificationError as e:
        return False, (e.verify_message or str(e))[:120]
    except (OSError, ssl.SSLError) as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def _forge(ca_cert_file: Path, ca_key_file: Path, name: str, out: Path) -> tuple[str, str]:
    """A server certificate for ``name`` signed by the CA, otherwise identical to the genuine leaf."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca = x509.load_pem_x509_certificate(ca_cert_file.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_file.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    out.mkdir(parents=True, exist_ok=True)
    c, k = out / f"{name}.crt", out / f"{name}.key"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    return str(c), str(k)


def measure_certificates(work: Path) -> list[dict]:
    rows = []
    for version in VERSIONS:
        addin = _load_addin(version, work / f"lib-{version}")
        p = addin.ensure_certificates()
        ok, why = _client_accepts(_serve_once(str(p["cert"]), str(p["key"])), p["ca_cert"], "localhost")
        rows.append({"version": VERSIONS[version], "group": version, "kind": "genuine", "name": "localhost",
                     "accepted": ok, "reason": why})
        for name in DOMAINS:
            cert, key = _forge(p["ca_cert"], p["ca_key"], name, work / f"forged-{version}")
            ok, why = _client_accepts(_serve_once(cert, key), p["ca_cert"], name)
            rows.append({"version": VERSIONS[version], "group": version, "kind": "forged", "name": name,
                         "accepted": ok, "reason": why})
        print(f"A  {VERSIONS[version]}: forged accepted "
              f"{sum(r['accepted'] for r in rows if r['group'] == version and r['kind'] == 'forged')}/{len(DOMAINS)}")
    return rows


# ======================================================================================
# B. Real-browser end-to-end runs
# ======================================================================================

def _previous_connector(work: Path) -> Path:
    dest = work / "connector-prev"
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", "--format=tar", PREV_REV, "browser_extension"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(archive)) as t:
        t.extractall(dest, filter="data")
    return dest / "browser_extension"


def _child_env() -> dict:
    """The e2e scripts isolate themselves (own library, port, profile) and must register the browser helper:
    no REVIEWHUNTER_HOME / REVIEWHUNTER_NO_SHORTCUTS left over from part A."""
    return {k: v for k, v in os.environ.items() if k not in ("REVIEWHUNTER_HOME", "REVIEWHUNTER_NO_SHORTCUTS")}


def measure_firefox(work: Path, args) -> list[dict]:
    rows = []
    sources = {"previous": _previous_connector(work), "current": ROOT / "browser_extension"}
    for run in range(1, E2E_REPEATS + 1):
        for version, source in sources.items():
            out = work / f"ff-{version}-{run}.json"
            cmd = [sys.executable, str(ROOT / "tools" / "extension_firefox_e2e.py"), "--firefox", args.firefox,
                   "--geckodriver", args.geckodriver, "--source", str(source), "--json", str(out)]
            if args.pylib:
                cmd += ["--pylib", args.pylib]
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, env=_child_env())
            data = (json.loads(out.read_text(encoding="utf-8")) if out.exists()
                    else {"checks": [], "error": "e2e did not run: " + proc.stderr.strip().splitlines()[-1][:250]})
            for c in data["checks"]:
                rows.append({"browser": "firefox", "version": VERSIONS[version], "group": version, "run": run,
                             "check": c["check"], "passed": c["ok"], "detail": c["detail"][:160]})
            rows.append({"browser": "firefox", "version": VERSIONS[version], "group": version, "run": run,
                         "check": "__error__", "passed": not data.get("error"), "detail": data.get("error", "")[:200]})
            print(f"B  firefox {VERSIONS[version]} run {run}: "
                  f"{sum(c['ok'] for c in data['checks'])}/{len(data['checks'])} {data.get('error', '')[:80]}")
    return rows


def measure_chromium() -> list[dict]:
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "extension_launch_e2e.py")], cwd=ROOT,
                          capture_output=True, text=True, timeout=900, env=_child_env())
    rows = []
    for line in proc.stdout.splitlines():
        if line.startswith(("PASS  ", "FAIL  ")):
            name = line[6:].split("  ")[0]
            rows.append({"browser": "chromium", "version": VERSIONS["current"], "group": "current", "run": 1,
                         "check": name, "passed": line.startswith("PASS"), "detail": line[6 + len(name):].strip()[:160]})
    print(f"B  chromium 2.11: {sum(r['passed'] for r in rows)}/{len(rows)}")
    return rows


def _write(name: str, rows: list[dict]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def measure(args) -> int:
    work = Path(tempfile.mkdtemp(prefix="rh-bench-N-"))
    if not args.skip_certificates:
        _write("N_certificate_forgery.csv", measure_certificates(work))
    _write("N_install_steps.csv", [{"target": t, "version": VERSIONS[v], "group": v,
                                    "user_actions": "" if a is None else a, "available": a is not None,
                                    "console_window": c, "procedure": n,
                                    "method": "counted from the documented procedure (not a user study)"}
                                   for t, v, a, c, n in INSTALL_STEPS])
    if args.firefox and args.geckodriver:
        _write("N_firefox_e2e.csv", measure_firefox(work, args))
    if not args.skip_chromium:
        _write("N_chromium_e2e.csv", measure_chromium())
    print(f"measurements written to {RESULTS}")
    return 0


# ======================================================================================
# Plot + statistics (needs matplotlib, pandas, scipy)
# ======================================================================================

def plot() -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    from scipy import stats

    plt.rcParams.update({"font.size": FONT_SIZE, "axes.spines.top": False, "axes.spines.right": False,
                         "font.family": "DejaVu Sans", "svg.fonttype": "none"})
    cert = pd.read_csv(RESULTS / "N_certificate_forgery.csv")
    ff = pd.read_csv(RESULTS / "N_firefox_e2e.csv")
    steps = pd.read_csv(RESULTS / "N_install_steps.csv")
    stat_rows = []

    # ---- A: forged certificates accepted
    forged = cert[cert.kind == "forged"]
    tab = [[int(forged[(forged.group == g)].accepted.sum()), int((~forged[(forged.group == g)].accepted.astype(bool)).sum())]
           for g in ("previous", "current")]
    odds, p_a = stats.fisher_exact(tab)
    stat_rows.append({"measure": "A forged certificates accepted", "previous": f"{tab[0][0]}/{sum(tab[0])}",
                      "current": f"{tab[1][0]}/{sum(tab[1])}", "test": "Fisher exact, two-sided", "p_value": p_a,
                      "assumption": "independent observations: one forged certificate per domain (met)"})
    genuine = cert[cert.kind == "genuine"].set_index("group").accepted
    stat_rows.append({"measure": "A genuine localhost certificate accepted (control)",
                      "previous": str(bool(genuine.get("previous"))), "current": str(bool(genuine.get("current"))),
                      "test": "control", "p_value": "", "assumption": ""})

    # ---- B: Firefox e2e
    checks = ff[ff.check != "__error__"]
    order = list(dict.fromkeys(checks[checks.group == "current"].check))
    per_run = (checks.groupby(["group", "run"]).passed.sum()
               .reindex(pd.MultiIndex.from_product([["previous", "current"], sorted(ff.run.unique())]), fill_value=0))
    total_planned = len(order)
    tab_b = []
    for g in ("previous", "current"):
        runs = per_run.loc[g]
        passed = int(runs.sum())
        tab_b.append([passed, total_planned * len(runs) - passed])
    _, p_b = stats.fisher_exact(tab_b)
    stat_rows.append({"measure": "B Firefox e2e checks passed (all runs)", "previous": f"{tab_b[0][0]}/{sum(tab_b[0])}",
                      "current": f"{tab_b[1][0]}/{sum(tab_b[1])}", "test": "Fisher exact, two-sided (descriptive)",
                      "p_value": p_b, "assumption": "checks within a run are NOT independent: p is descriptive"})
    for g in ("previous", "current"):
        stat_rows.append({"measure": f"B Firefox checks passed per run ({VERSIONS[g]})",
                          "previous" if g == "previous" else "current": " ".join(str(int(v)) for v in per_run.loc[g]),
                          "test": f"out of {total_planned} planned checks", "p_value": "", "assumption": ""})
    chromium_file = RESULTS / "N_chromium_e2e.csv"
    if chromium_file.exists():
        ch = pd.read_csv(chromium_file)
        stat_rows.append({"measure": "B Chromium e2e 2.11 (regression check)", "current": f"{int(ch.passed.sum())}/{len(ch)}",
                          "test": "single run", "p_value": "", "assumption": ""})

    # ---- figure
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.7), gridspec_kw={"width_ratios": [0.55, 1.0, 1.25], "wspace": 1.05})
    ax = axes[0]
    for i, g in enumerate(("previous", "current")):
        acc = tab[i][0] / sum(tab[i]) * 100
        ax.bar(i, acc, width=BAR_WIDTH, color=COLORS[g])
        ax.text(i, acc + 3, f"{tab[i][0]}/{sum(tab[i])}", ha="center", va="bottom", fontsize=FONT_SIZE - 1)
    ax.set_xticks([0, 1], [VERSIONS["previous"], VERSIONS["current"]])
    ax.set_ylim(0, 125)
    ax.set_yticks([0, 50, 100])
    ax.set_ylabel("forged certificates accepted by\nthe Word add-in CA (%)")
    ax.set_title("A  Forged sites", loc="left", fontsize=FONT_SIZE + 1, fontweight="bold")
    ax.text(0.5, 114, f"Fisher p = {p_a:.1e}", ha="center", fontsize=FONT_SIZE - 1)

    ax = axes[1]
    runs = sorted(ff.run.unique())
    labels = []
    for gi, g in enumerate(("previous", "current")):
        for r in runs:
            col = gi * (len(runs) + 1) + (r - 1)
            labels.append((col, str(r)))
            got = checks[(checks.group == g) & (checks.run == r)].set_index("check").passed
            for ci, name in enumerate(order):
                v = got.get(name)
                color = COLORS["na"] if v is None or (isinstance(v, float) and v != v) else (COLORS["pass"] if bool(v) else COLORS["fail"])
                ax.add_patch(plt.Rectangle((col, ci), 0.9, 0.9, color=color, lw=0))
    ax.set_xlim(-0.2, 2 * len(runs) + 0.9)
    ax.set_ylim(len(order) + 0.1, -0.2)
    ax.set_xticks([c + 0.45 for c, _ in labels], [l for _, l in labels], fontsize=FONT_SIZE - 1.5)
    for gi, g in enumerate(("previous", "current")):   # version under each group of runs
        ax.text(gi * (len(runs) + 1) + len(runs) / 2 - 0.05, len(order) + 0.9, f"{VERSIONS[g]} (run)",
                ha="center", va="top", fontsize=FONT_SIZE - 1)
    ax.set_yticks([i + 0.45 for i in range(len(order))], [SHORT_CHECKS.get(n, n[:24]) for n in order],
                  fontsize=FONT_SIZE - 1)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(f"B  Real Firefox: {tab_b[0][0]}/{sum(tab_b[0])} vs {tab_b[1][0]}/{sum(tab_b[1])} checks",
                 loc="left", fontsize=FONT_SIZE + 1, fontweight="bold")
    keys = [plt.Rectangle((0, 0), 1, 1, color=COLORS[k]) for k in ("pass", "fail", "na")]
    ax.legend(keys, ["pass", "fail", "not reached"], frameon=False, fontsize=FONT_SIZE - 1.5, ncol=3,
              loc="upper center", bbox_to_anchor=(0.5, -0.2), handlelength=0.9, columnspacing=0.8)

    ax = axes[2]
    targets = list(dict.fromkeys(steps.target))
    for gi, g in enumerate(("previous", "current")):
        sub = steps[steps.group == g].set_index("target")
        for ti, t in enumerate(targets):
            y = ti + (gi - 0.5) * BAR_WIDTH * 1.1
            row = sub.loc[t]
            if bool(row.available):
                ax.barh(y, row.user_actions, height=BAR_WIDTH, color=COLORS[g])
                ax.text(row.user_actions + 0.15, y, f"{int(row.user_actions)}{' + console' if row.console_window else ''}",
                        va="center", fontsize=FONT_SIZE - 1.5)
            else:
                ax.text(0.1, y, "not available", va="center", fontsize=FONT_SIZE - 1.5, color="#9aa3b2")
    ax.set_yticks(range(len(targets)), targets, fontsize=FONT_SIZE - 1)
    ax.invert_yaxis()
    ax.set_xlim(0, 9.5)
    ax.set_xlabel("user actions to install (documented)")
    ax.set_title("C  Install procedure", loc="left", fontsize=FONT_SIZE + 1, fontweight="bold")
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[g]) for g in ("previous", "current")]
    ax.legend(handles, [VERSIONS["previous"], VERSIONS["current"]], frameon=False, fontsize=FONT_SIZE - 1,
              loc="lower right")
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.2, top=0.88)
    for ext in ("png", "pdf", "svg"):
        fig.savefig(RESULTS / f"N_addons.{ext}", dpi=FIG_DPI, bbox_inches="tight")
    pd.DataFrame(stat_rows).to_csv(RESULTS / "N_statistics.csv", index=False)
    print(pd.DataFrame(stat_rows).to_string())
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["measure", "plot"])
    p.add_argument("--firefox", default="")
    p.add_argument("--geckodriver", default="")
    p.add_argument("--pylib", default="")
    p.add_argument("--skip-chromium", action="store_true")
    p.add_argument("--skip-certificates", action="store_true")
    args = p.parse_args()
    return measure(args) if args.step == "measure" else plot()


if __name__ == "__main__":
    sys.exit(main())
