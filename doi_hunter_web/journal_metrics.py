"""Journal impact metrics: a free, honest substitute for the proprietary Journal Impact Factor.

The real Journal Impact Factor (JIF) is a Clarivate trademark computed from Web of Science data behind
a paywall (Journal Citation Reports); it cannot be scraped or redistributed. What this module fetches
instead, from the free and openly licensed `OpenAlex <https://openalex.org>`_ API, is the journal's
**2-year mean citedness** (citations in year Y to papers published in Y-1/Y-2, divided by the number of
those papers) plus its h-index and works count. It correlates with JIF but is not numerically the same
scale, so it is always labelled "OpenAlex" in the UI, never presented as "the" impact factor. A user
whose institution licenses Web of Science can type the real JIF in as a **manual value**, which then
takes priority everywhere.

Metrics are cached per journal (keyed by ISSN, or by a normalised name when no ISSN is known) in the
``journal_metrics`` table, so a library with many items in the same journal costs one network call, and
a fresh value is not re-fetched for ``CACHE_DAYS_FOUND`` / ``CACHE_DAYS_NOT_FOUND`` days. The resolved
value is then copied onto every item of that journal (``items.impact_factor`` / ``_source`` / ``_year``)
so the item list can sort and filter on it with a plain SQL column, exactly like ``citation_key``.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Optional

import requests

from .db import get_conn

log = logging.getLogger("doihunter.journal_metrics")

# ============================================================================ PARAMETERS
API_BASE = "https://api.openalex.org/sources"
REQUEST_TIMEOUT_S = 15
CACHE_DAYS_FOUND = 120        # a matched journal's citation stats change slowly
CACHE_DAYS_NOT_FOUND = 14     # retry an unmatched journal sooner (it may have been a typo, now fixed)
NAME_MATCH_THRESHOLD = 0.87   # difflib ratio required to accept a name-only match
SEARCH_RESULTS = 8            # candidates shown in the "look up a journal" search
REFRESH_PAUSE_S = 0.15        # politeness pause between distinct journals in a bulk refresh
# ============================================================================


@dataclass
class JournalRow:
    """One cached (or manual-only) journal metrics record."""

    key: str
    issn: Optional[str]
    name: Optional[str]
    openalex_id: Optional[str]
    citations_2yr: Optional[float]
    h_index: Optional[int]
    works_count: Optional[int]
    cited_by_count: Optional[int]
    manual_value: Optional[float]
    manual_year: Optional[int]
    manual_note: Optional[str]
    fetched_at: Optional[str]
    found: bool

    def display(self) -> dict:
        """What the UI shows: the manual value wins when set, else the OpenAlex proxy."""
        if self.manual_value is not None:
            year = f" {self.manual_year}" if self.manual_year else ""
            return {"value": round(self.manual_value, 3), "source": "manual",
                    "label": f"{self.manual_value:g}{year} (manual)", "year": self.manual_year,
                    "note": self.manual_note or ""}
        if self.citations_2yr is not None:
            return {"value": round(self.citations_2yr, 3), "source": "openalex",
                    "label": f"{self.citations_2yr:.1f} (OpenAlex, 2-yr)", "year": None, "note": ""}
        return {"value": None, "source": None, "label": "", "year": None, "note": ""}


_ISSN_RX = re.compile(r"^\d{4}-?\d{3}[\dXx]$")
_PAREN_RX = re.compile(r"\s*\([^)]*\)\s*$")   # trailing "(Print)", "(Berl)", "(N Y)"...


def normalize_issn(raw: Optional[str]) -> Optional[str]:
    """"0269-8811", "02698811" -> "0269-8811"; None for anything that is not one ISSN."""
    if not raw:
        return None
    # a value may hold several ISSNs ("1234-5678, 9876-5432") or come from a JSON list already split upstream
    candidate = re.split(r"[,;/]\s*", str(raw).strip())[0].strip().upper()
    digits = candidate.replace("-", "")
    if not re.fullmatch(r"\d{7}[\dX]", digits):
        return None
    return f"{digits[:4]}-{digits[4:]}"


def _normalize_name(name: str) -> str:
    """Casefold, drop a trailing parenthetical qualifier, collapse whitespace and punctuation."""
    name = _PAREN_RX.sub("", name or "")
    name = re.sub(r"[.:,]", " ", name)
    return re.sub(r"\s+", " ", name).strip().casefold()


def cache_key(issn: Optional[str], journal: Optional[str]) -> Optional[str]:
    norm_issn = normalize_issn(issn)
    if norm_issn:
        return f"issn:{norm_issn}"
    norm_name = _normalize_name(journal or "")
    return f"name:{norm_name}" if norm_name else None


# ---------------------------------------------------------------------------- OpenAlex

def _settings() -> dict:
    try:
        from . import config

        return config.get_settings()
    except Exception:
        return {}


def _user_agent() -> dict:
    email = (_settings().get("unpaywall_email") or "").strip()
    ua = f"ReviewHunter/2.2 (mailto:{email})" if email else "ReviewHunter/2.2"
    return {"User-Agent": ua, "Accept": "application/json"}


def _get(params: dict) -> Optional[list[dict]]:
    try:
        r = requests.get(API_BASE, params=params, timeout=REQUEST_TIMEOUT_S, headers=_user_agent())
        if not r.ok:
            log.info(f"OpenAlex sources {params}: HTTP {r.status_code}")
            return None
        return r.json().get("results") or []
    except (requests.RequestException, ValueError) as e:
        log.info(f"OpenAlex sources lookup failed for {params}: {e}")
        return None


def _to_row(res: dict) -> dict:
    stats = res.get("summary_stats") or {}
    issns = res.get("issn") or ([res["issn_l"]] if res.get("issn_l") else [])
    return {
        "issn": normalize_issn(res.get("issn_l")) or normalize_issn(issns[0] if issns else None),
        "name": res.get("display_name") or "",
        "openalex_id": (res.get("id") or "").rsplit("/", 1)[-1],
        "citations_2yr": stats.get("2yr_mean_citedness"),
        "h_index": stats.get("h_index"),
        "works_count": res.get("works_count"),
        "cited_by_count": res.get("cited_by_count"),
    }


def _best_by_name(name: str, results: list[dict]) -> Optional[dict]:
    target = _normalize_name(name)
    if not target:
        return None
    scored = []
    for res in results:
        cand = _normalize_name(res.get("display_name") or "")
        if not cand:
            continue
        ratio = 1.0 if cand == target else difflib.SequenceMatcher(None, target, cand).ratio()
        scored.append((ratio, res.get("works_count") or 0, res))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], -t[1]))
    best_ratio, _works, best = scored[0]
    return best if best_ratio >= NAME_MATCH_THRESHOLD else None


def fetch_remote(issn: Optional[str] = None, journal: Optional[str] = None) -> Optional[dict]:
    """Query OpenAlex for one journal. ISSN gives an exact match; a name is matched by similarity."""
    norm_issn = normalize_issn(issn)
    if norm_issn:
        results = _get({"filter": f"issn:{norm_issn}"})
        if results:
            return _to_row(results[0])
        # a valid ISSN OpenAlex does not recognise: still try the name, if any
    name = (journal or "").strip()
    if not name:
        return None
    results = _get({"search": name, "per_page": 10})
    if not results:
        return None
    best = _best_by_name(name, results)
    return _to_row(best) if best else None


def search(query: str, limit: int = SEARCH_RESULTS) -> list[dict]:
    """Live candidates for the "look up a journal" UI (never cached until the user picks one)."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    results = _get({"search": query, "per_page": limit}) or []
    return [_to_row(r) for r in results]


# ---------------------------------------------------------------------------- cache table

def _row_from_db(r) -> JournalRow:
    return JournalRow(key=r["key"], issn=r["issn"], name=r["name"], openalex_id=r["openalex_id"],
                      citations_2yr=r["citations_2yr"], h_index=r["h_index"], works_count=r["works_count"],
                      cited_by_count=r["cited_by_count"], manual_value=r["manual_value"], manual_year=r["manual_year"],
                      manual_note=r["manual_note"], fetched_at=r["fetched_at"], found=bool(r["found"]))


def get_cached(issn: Optional[str] = None, journal: Optional[str] = None) -> Optional[JournalRow]:
    key = cache_key(issn, journal)
    if not key:
        return None
    row = get_conn().execute("SELECT * FROM journal_metrics WHERE key=?", (key,)).fetchone()
    return _row_from_db(row) if row else None


def _is_fresh(fetched_at: Optional[str], found: bool) -> bool:
    if not fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - fetched).total_seconds() / 86400
    return age_days < (CACHE_DAYS_FOUND if found else CACHE_DAYS_NOT_FOUND)


def get_or_fetch(issn: Optional[str] = None, journal: Optional[str] = None, *, force: bool = False) -> Optional[JournalRow]:
    """Cached lookup, refreshing from OpenAlex when stale (or ``force``). None only when there is no key at all."""
    key = cache_key(issn, journal)
    if not key:
        return None
    conn = get_conn()
    existing = get_cached(issn, journal)
    if existing and not force and _is_fresh(existing.fetched_at, existing.found):
        return existing
    remote = fetch_remote(issn, journal)
    now = datetime.now(timezone.utc).isoformat()
    if remote:
        resolved_key = cache_key(remote.get("issn"), remote.get("name")) or key
        row_values = (remote["issn"], remote["name"], remote["openalex_id"], remote["citations_2yr"],
                     remote["h_index"], remote["works_count"], remote["cited_by_count"], now)
        upsert_sql = (
            "INSERT INTO journal_metrics (key, issn, name, openalex_id, citations_2yr, h_index, works_count, "
            "cited_by_count, fetched_at, found) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(key) DO UPDATE SET issn=excluded.issn, name=excluded.name, openalex_id=excluded.openalex_id, "
            "citations_2yr=excluded.citations_2yr, h_index=excluded.h_index, works_count=excluded.works_count, "
            "cited_by_count=excluded.cited_by_count, fetched_at=excluded.fetched_at, found=1"
        )
        conn.execute(upsert_sql, (resolved_key, *row_values))
        if resolved_key != key:
            # The lookup key (an item's own ISSN, or a bare journal name) resolved to a different
            # canonical key at OpenAlex: alias it too, so the next lookup for this exact item hits the
            # cache directly instead of re-querying the network every time.
            conn.execute(upsert_sql, (key, *row_values))
        return get_cached(issn, journal)
    conn.execute(
        "INSERT INTO journal_metrics (key, issn, name, fetched_at, found) VALUES (?, ?, ?, ?, 0) "
        "ON CONFLICT(key) DO UPDATE SET fetched_at=excluded.fetched_at, found=0 WHERE journal_metrics.manual_value IS NULL",
        (key, normalize_issn(issn), journal, now),
    )
    return get_cached(issn, journal)


def set_manual(issn: Optional[str], journal: Optional[str], *, value: Optional[float], year: Optional[int] = None,
              note: Optional[str] = None) -> JournalRow:
    """Record (or clear, with ``value=None``) a manually entered value for a journal."""
    key = cache_key(issn, journal)
    if not key:
        raise ValueError("a journal name or ISSN is required")
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO journal_metrics (key, issn, name, manual_value, manual_year, manual_note, fetched_at, found) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1) "
        "ON CONFLICT(key) DO UPDATE SET manual_value=excluded.manual_value, manual_year=excluded.manual_year, "
        "manual_note=excluded.manual_note, issn=COALESCE(journal_metrics.issn, excluded.issn), "
        "name=COALESCE(journal_metrics.name, excluded.name)",
        (key, normalize_issn(issn), journal, value, year, note, now),
    )
    return get_cached(issn, journal)


# ---------------------------------------------------------------------------- applying to items

def apply_to_items_of_journal(issn: Optional[str], journal: Optional[str], row: Optional[JournalRow]) -> int:
    """Write the resolved display value onto every non-deleted item sharing this journal identity.

    Matching mirrors how the value was looked up: by ISSN when the item has one, else by normalised
    journal name (SQLite has no casefold(), so the exact comparison runs in Python), so an item never
    receives another journal's number by accident. A cheap SQL prefilter keeps this fast on large
    libraries without needing an extra index.
    """
    conn = get_conn()
    display = row.display() if row else {"value": None, "source": None, "year": None}
    norm_issn = normalize_issn(issn)
    if norm_issn:
        digits = norm_issn.replace("-", "")
        candidates = conn.execute(
            "SELECT id, issn FROM items WHERE deleted_at IS NULL AND issn IS NOT NULL AND issn LIKE ?",
            (f"%{digits[:4]}%",),
        ).fetchall()
        ids = [c["id"] for c in candidates if normalize_issn(c["issn"]) == norm_issn]
    elif journal and journal.strip():
        prefix = journal.strip()[:12]
        candidates = conn.execute(
            "SELECT id, journal FROM items WHERE deleted_at IS NULL AND journal LIKE ?", (f"%{prefix}%",)
        ).fetchall()
        ids = [c["id"] for c in candidates if _normalize_name(c["journal"]) == _normalize_name(journal)]
    else:
        return 0
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE items SET impact_factor=?, impact_factor_source=?, impact_factor_year=? WHERE id IN ({marks})",
                [display["value"], display["source"], display["year"], *ids])
    return len(ids)


def refresh_item(item_id: int, *, force: bool = False) -> dict:
    """Resolve and apply the metric for one item's journal. {ok, value, source, reason}."""
    row = get_conn().execute("SELECT journal, issn FROM items WHERE id=? AND deleted_at IS NULL", (item_id,)).fetchone()
    if not row:
        return {"ok": False, "reason": "item not found"}
    journal, issn = row["journal"], row["issn"]
    if not (journal or "").strip() and not normalize_issn(issn):
        return {"ok": False, "reason": "no journal on this item"}
    metrics = get_or_fetch(issn, journal, force=force)
    n = apply_to_items_of_journal(issn, journal, metrics)
    disp = metrics.display() if metrics else {"value": None, "source": None}
    if disp["value"] is None:
        return {"ok": False, "reason": f'"{journal or issn}" was not found in OpenAlex; you can set a value manually'}
    return {"ok": True, "value": disp["value"], "source": disp["source"], "label": disp["label"], "items_updated": n}


def refresh_many(item_ids: Optional[list[int]] = None, *, force: bool = False,
                 progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """Refresh every distinct journal among ``item_ids`` (default: every item missing a value).

    One network call per distinct journal, however many items share it. Returns a summary; a matching
    reason is recorded on the caller-visible progress channel so a bulk run can be watched live.
    """
    conn = get_conn()
    if item_ids:
        marks = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            f"SELECT DISTINCT journal, issn FROM items WHERE id IN ({marks}) AND deleted_at IS NULL "
            f"AND (journal IS NOT NULL AND journal != '' OR issn IS NOT NULL AND issn != '')", item_ids
        ).fetchall()
    else:
        cond = "" if force else "AND impact_factor IS NULL "
        rows = conn.execute(
            f"SELECT DISTINCT journal, issn FROM items WHERE deleted_at IS NULL {cond}"
            "AND (journal IS NOT NULL AND journal != '' OR issn IS NOT NULL AND issn != '')"
        ).fetchall()
    journals = [(r["journal"], r["issn"]) for r in rows]
    updated_items = 0
    matched_journals = 0
    unmatched: list[str] = []
    for i, (journal, issn) in enumerate(journals, start=1):
        if progress:
            progress("journal", {"index": i, "total": len(journals), "journal": journal or issn or ""})
        metrics = get_or_fetch(issn, journal, force=force)
        n = apply_to_items_of_journal(issn, journal, metrics)
        disp = metrics.display() if metrics else {"value": None}
        if disp["value"] is not None:
            matched_journals += 1
            updated_items += n
        else:
            unmatched.append(journal or issn or "(unknown)")
        if i < len(journals):
            time.sleep(REFRESH_PAUSE_S)
    return {"journals": len(journals), "matched": matched_journals, "items_updated": updated_items,
            "unmatched": unmatched[:30]}
