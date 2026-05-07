"""Shared utilities for the ReviewHunter CLI and web app."""
import os
import re
import logging
from pathlib import Path
from typing import Optional

import requests

from .netinfo import get_headers

CROSSREF_API = "https://api.crossref.org/works"
SAFE_NAME_RE = re.compile(r'[\\/*?:"<>|\r\n\t]')


def sanitize_filename(name: str, max_len: int = 180) -> str:
    """Strip filesystem-hostile characters and trim length."""
    name = SAFE_NAME_RE.sub("", name).strip().rstrip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "untitled"


def get_doi_by_title(title: str, mailto: Optional[str] = None) -> Optional[str]:
    """Look up a DOI for a free-text title via CrossRef. Returns the top match's DOI."""
    params = {"query.title": title, "rows": 1}
    if mailto:
        params["mailto"] = mailto
    try:
        r = requests.get(CROSSREF_API, params=params, headers=get_headers(), timeout=15)
        if r.status_code != 200:
            return None
        items = r.json().get("message", {}).get("items", [])
        if items:
            return items[0].get("DOI")
    except Exception as e:
        logging.error(f"CrossRef lookup failed for '{title}': {e}")
    return None


def download_file(url: str, file_name: str, download_folder: str) -> str:
    """Download a PDF to download_folder/file_name.pdf. Returns 'success', 'skipped', or 'failed'."""
    Path(download_folder).mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(file_name)
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    target = os.path.join(download_folder, safe_name)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        try:
            print(f"[v] File already exists. Skipping: {safe_name}")
        except UnicodeEncodeError:
            print("[v] File already exists. Skipping (non-ascii filename).")
        return "skipped"
    try:
        with requests.get(url, headers=get_headers(), stream=True, timeout=60) as r:
            if r.status_code != 200:
                logging.error(f"HTTP {r.status_code} fetching {url}")
                return "failed"
            with open(target, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        try:
            print(f"[v] Downloaded: {safe_name}")
        except UnicodeEncodeError:
            print("[v] Downloaded a file (non-ascii filename).")
        return "success"
    except Exception as e:
        logging.error(f"Download failed for {url}: {e}")
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        return "failed"


def log_failed_downloads(failed: list, path: str) -> None:
    """Idempotent: write the list of failed identifiers to disk if any."""
    if not failed:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            for item in dict.fromkeys(failed):  # de-dup preserving order
                f.write(f"{item}\n")
    except Exception as e:
        logging.error(f"Could not write {path}: {e}")
