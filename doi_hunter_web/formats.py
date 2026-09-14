"""Bibliographic import / export formats.

Import (text -> list of create payloads for ``catalog.create_item``):
    BibTeX / BibLaTeX, RIS, CSL-JSON (auto-detected by ``detect_format``).
Export (items -> text):
    BibTeX, BibLaTeX, RIS, CSL-JSON, CSV, Microsoft Word bibliography sources XML.

Round-trip quality is covered by ``tests/test_formats.py``.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Iterable
from xml.sax.saxutils import escape as xml_escape

from . import catalog
from .csl import csl_to_item, item_to_csl

# ======================================================================================
# Detection
# ======================================================================================


def detect_format(text: str, filename: str = "") -> str:
    name = (filename or "").lower()
    if name.endswith((".bib", ".bibtex", ".biblatex")):
        return "bibtex"
    if name.endswith((".ris", ".enw")):
        return "ris"
    if name.endswith(".json"):
        return "csljson"
    head = (text or "").lstrip()[:2000]
    if head.startswith("[") or head.startswith("{"):
        return "csljson"
    if re.search(r"^TY\s{2}-", head, flags=re.M):
        return "ris"
    if re.search(r"@\w+\s*[{(]", head):
        return "bibtex"
    return "identifiers"   # plain list of DOIs / ISBNs / PMIDs / titles


def parse(text: str, fmt: str | None = None, filename: str = "") -> list[dict]:
    fmt = fmt or detect_format(text, filename)
    if fmt == "bibtex":
        return parse_bibtex(text)
    if fmt == "ris":
        return parse_ris(text)
    if fmt == "csljson":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("items") or [data]
        return [csl_to_item(d) for d in data if isinstance(d, dict)]
    raise ValueError(f"unsupported import format: {fmt}")


# ======================================================================================
# BibTeX
# ======================================================================================

_BIB_TO_TYPE = {
    "article": "journalArticle", "book": "book", "booklet": "book", "mvbook": "book", "inbook": "bookSection",
    "incollection": "bookSection", "inproceedings": "conferencePaper", "conference": "conferencePaper",
    "proceedings": "book", "phdthesis": "thesis", "mastersthesis": "thesis", "thesis": "thesis",
    "techreport": "report", "report": "report", "online": "webpage", "electronic": "webpage", "www": "webpage",
    "misc": "document", "unpublished": "manuscript", "manual": "document", "patent": "patent",
    "software": "computerProgram", "dataset": "dataset", "periodical": "journalArticle",
}

_TYPE_TO_BIB = {
    "journalArticle": "article", "magazineArticle": "article", "newspaperArticle": "article", "book": "book",
    "bookSection": "incollection", "encyclopediaArticle": "incollection", "conferencePaper": "inproceedings",
    "thesis": "phdthesis", "report": "techreport", "manuscript": "unpublished", "preprint": "misc",
    "webpage": "misc", "blogPost": "misc", "dataset": "misc", "computerProgram": "misc", "patent": "misc",
    "document": "misc", "presentation": "misc", "letter": "misc", "videoRecording": "misc",
}
_TYPE_TO_BIBLATEX = {**_TYPE_TO_BIB, "thesis": "thesis", "report": "report", "webpage": "online",
                     "blogPost": "online", "dataset": "dataset", "computerProgram": "software",
                     "patent": "patent", "preprint": "online"}

_LATEX_ACCENTS = {
    r"\'": "\u0301", r"\`": "\u0300", r"\^": "\u0302", r'\"': "\u0308", r"\~": "\u0303", r"\c": "\u0327",
    r"\=": "\u0304", r"\.": "\u0307", r"\u": "\u0306", r"\v": "\u030c", r"\H": "\u030b", r"\k": "\u0328",
}
_LATEX_SYMBOLS = {r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\ae": "æ", r"\AE": "Æ", r"\aa": "å", r"\AA": "Å",
                  r"\l": "ł", r"\L": "Ł", r"\oe": "œ", r"\OE": "Œ", r"\&": "&", r"\%": "%", r"\$": "$",
                  r"\#": "#", r"\_": "_", "--": "–", r"\textendash": "–", r"\textemdash": "—"}


def latex_to_unicode(s: str) -> str:
    import unicodedata

    if not s:
        return ""
    for cmd, combining in _LATEX_ACCENTS.items():
        pattern = re.escape(cmd) + r"\s*\{?\\?([A-Za-z])\}?"
        s = re.sub(pattern, lambda m: unicodedata.normalize("NFC", m.group(1) + combining), s)
    for cmd, char in sorted(_LATEX_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        s = re.sub(re.escape(cmd) + r"(?![A-Za-z])\{?\}?", lambda _m, c=char: c, s)
    s = re.sub(r"\\(?:textit|textbf|emph|textsc|mathrm|text)\{([^{}]*)\}", r"\1", s)
    s = s.replace("{", "").replace("}", "").replace("~", " ")
    return re.sub(r"\s+", " ", s).strip()


def _bib_names(raw: str, creator_type: str) -> list[dict]:
    out = []
    for name in re.split(r"\s+and\s+", raw or "", flags=re.I):
        name = name.strip()
        if not name:
            continue
        if name.startswith("{") and name.endswith("}") and "," not in name[1:-1]:
            out.append({"creatorType": creator_type, "name": latex_to_unicode(name)})
            continue
        clean = latex_to_unicode(name)
        if name.lower() == "others":
            continue
        out.append(catalog.parse_creator_string(clean, creator_type))
    return out


def parse_bibtex(text: str) -> list[dict]:
    import bibtexparser
    from bibtexparser.bparser import BibTexParser

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    parser.homogenize_fields = False
    db = bibtexparser.loads(text, parser=parser)
    items = []
    for e in db.entries:
        f = {k.lower(): v for k, v in e.items()}
        bib_type = f.get("entrytype", "misc").lower()
        item_type = _BIB_TO_TYPE.get(bib_type, "document")
        if bib_type == "misc" and (f.get("eprint") or "arxiv" in (f.get("archiveprefix", "") + f.get("howpublished", "")).lower()):
            item_type = "preprint"
        if bib_type == "misc" and f.get("url") and not f.get("journal") and not f.get("doi"):
            item_type = "webpage"
        creators = _bib_names(f.get("author", ""), "author") + _bib_names(f.get("editor", ""), "editor") \
            + _bib_names(f.get("translator", ""), "translator")
        date = f.get("date") or f.get("year", "")
        month = f.get("month", "")
        if f.get("year") and month and not f.get("date"):
            months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
            mm = month.strip().lower()[:3]
            if mm in months:
                date = f"{f['year']}-{months.index(mm) + 1:02d}"
            elif mm.isdigit():
                date = f"{f['year']}-{int(mm):02d}"
        container = f.get("journal") or f.get("journaltitle") or f.get("booktitle") or ""
        fields: dict[str, Any] = {}
        if item_type == "thesis":
            fields["thesisType"] = "Master's thesis" if bib_type == "mastersthesis" else (f.get("type") or "PhD thesis")
        if item_type == "report" and f.get("number"):
            fields["reportNumber"] = f["number"]
        if item_type == "conferencePaper" and f.get("eventtitle"):
            fields["conferenceName"] = latex_to_unicode(f["eventtitle"])
        if f.get("eprint"):
            fields["archiveID"] = f"arXiv:{f['eprint']}" if "arxiv" in f.get("archiveprefix", "arxiv").lower() else f["eprint"]
        keywords = [latex_to_unicode(k).strip() for k in re.split(r"[,;]", f.get("keywords", "")) if k.strip()]
        isbn = [p for p in re.split(r"[,;\s]+", f.get("isbn", "")) if p]
        extra = []
        if f.get("note"):
            extra.append(latex_to_unicode(f["note"]))
        items.append({
            "item_type": item_type,
            "citation_key": e.get("ID") or None,
            "title": latex_to_unicode(f.get("title", "Untitled")),
            "creators": creators,
            "journal": latex_to_unicode(container),
            "publisher": latex_to_unicode(f.get("publisher") or f.get("school") or f.get("institution")
                                          or f.get("organization") or ""),
            "place": latex_to_unicode(f.get("address") or f.get("location") or ""),
            "volume": f.get("volume", ""),
            "issue": f.get("number", "") if item_type == "journalArticle" else f.get("issue", ""),
            "pages": latex_to_unicode(f.get("pages", "")).replace("–", "-"),
            "edition": f.get("edition", ""),
            "series": latex_to_unicode(f.get("series", "")),
            "abstract": latex_to_unicode(f.get("abstract", "")),
            "doi": catalog.normalize_doi(f.get("doi")) or None,
            "url": f.get("url", ""),
            "issn": f.get("issn", ""),
            "isbn": isbn,
            "language": f.get("language") or f.get("langid") or "",
            "date": date,
            "year": catalog.year_from_date(date),
            "extra": "\n".join(extra),
            "pmid": f.get("pmid", ""),
            "fields": fields,
            "tags": keywords,
            "_file": f.get("file", ""),
        })
    return items


_BIB_SPECIAL = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}


def _bib_escape(s: Any, *, protect_caps: bool = False) -> str:
    s = str(s or "")
    s = "".join(_BIB_SPECIAL.get(ch, ch) for ch in s)
    if protect_caps:
        # Protect acronyms / proper nouns with internal capitals (e.g. {DNA}, {fMRI}) from style lowercasing.
        s = re.sub(r"\b([A-Za-z]*[A-Z][A-Za-z]*[A-Z0-9][A-Za-z0-9]*)\b", r"{\1}", s)
    return s


def _bib_name(c: dict) -> str:
    if c.get("name"):
        return "{" + _bib_escape(c["name"]) + "}"
    last, first = c.get("lastName") or "", c.get("firstName") or ""
    return f"{_bib_escape(last)}, {_bib_escape(first)}" if first else _bib_escape(last)


def export_bibtex(items: Iterable[dict], *, biblatex: bool = False) -> str:
    out = []
    for it in items:
        t = catalog.normalize_type(it.get("item_type"))
        fields_extra = it.get("fields") or {}
        entry_type = (_TYPE_TO_BIBLATEX if biblatex else _TYPE_TO_BIB).get(t, "misc")
        if t == "thesis" and not biblatex and "master" in str(fields_extra.get("thesisType", "")).lower():
            entry_type = "mastersthesis"
        key = it.get("citation_key") or f"item{it.get('id')}"
        f: list[tuple[str, str]] = []

        def add(name: str, value: Any, *, protect: bool = False) -> None:
            if value not in (None, "", []):
                f.append((name, _bib_escape(value, protect_caps=protect)))

        add("title", it.get("title"), protect=True)
        creators = it.get("creators") or catalog.clean_creators(it.get("authors") or [])
        for role, field in (("author", "author"), ("editor", "editor"), ("translator", "translator")):
            names = [_bib_name(c) for c in creators if c.get("creatorType") == role
                     or (role == "author" and c.get("creatorType") in ("programmer", "inventor", "presenter"))]
            if names:
                f.append((field, " and ".join(names)))
        container = it.get("journal")
        if t in ("journalArticle", "magazineArticle", "newspaperArticle"):
            add("journaltitle" if biblatex else "journal", container)
        elif container:
            add("booktitle", container)
        if t == "thesis":
            add("school" if not biblatex else "institution", it.get("publisher"))
            add("type", fields_extra.get("thesisType"))
        elif t == "report":
            add("institution", it.get("publisher"))
            add("number", fields_extra.get("reportNumber"))
        else:
            add("publisher", it.get("publisher"))
        add("location" if biblatex else "address", it.get("place"))
        add("volume", it.get("volume"))
        add("number", it.get("issue"))
        add("pages", str(it.get("pages") or "").replace("-", "--") if it.get("pages") else "")
        add("edition", it.get("edition"))
        add("series", it.get("series"))
        if biblatex and it.get("date"):
            add("date", it.get("date"))
        else:
            add("year", it.get("year"))
            m = re.match(r"^\d{4}-(\d{2})", str(it.get("date") or ""))
            if m:
                add("month", ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                              "dec"][int(m.group(1)) - 1])
        add("doi", it.get("doi"))
        if it.get("url"):
            f.append(("url", str(it["url"])))
        add("issn", it.get("issn"))
        isbn = it.get("isbn") or []
        add("isbn", ", ".join(isbn) if isinstance(isbn, list) else isbn)
        add("langid" if biblatex else "language", it.get("language"))
        add("abstract", it.get("abstract"))
        if fields_extra.get("archiveID", "").lower().startswith("arxiv:"):
            add("eprint", fields_extra["archiveID"][6:])
            add("archiveprefix" if not biblatex else "eprinttype", "arXiv")
        if it.get("arxiv_id"):
            add("eprint", it["arxiv_id"])
            add("archiveprefix" if not biblatex else "eprinttype", "arXiv")
        tags = [t_["name"] for t_ in it.get("tags") or []]
        add("keywords", ", ".join(tags))
        add("note", it.get("extra"))
        if it.get("file_path"):
            f.append(("file", str(it["file_path"]).replace("\\", "/")))
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in f)
        out.append(f"@{entry_type}{{{key},\n{body}\n}}\n")
    return "\n".join(out)


# ======================================================================================
# RIS
# ======================================================================================

_RIS_TO_TYPE = {
    "JOUR": "journalArticle", "JFULL": "journalArticle", "EJOUR": "journalArticle", "ABST": "journalArticle",
    "BOOK": "book", "EBOOK": "book", "EDBOOK": "book", "CHAP": "bookSection", "ECHAP": "bookSection",
    "CONF": "conferencePaper", "CPAPER": "conferencePaper", "THES": "thesis", "RPRT": "report",
    "ELEC": "webpage", "WEB": "webpage", "BLOG": "blogPost", "MGZN": "magazineArticle", "NEWS": "newspaperArticle",
    "ENCYC": "encyclopediaArticle", "DATA": "dataset", "COMP": "computerProgram", "PAT": "patent",
    "SLIDE": "presentation", "UNPB": "manuscript", "MANSCPT": "manuscript", "PCOMM": "letter",
    "VIDEO": "videoRecording", "GEN": "document", "STAND": "report", "INPR": "preprint",
}
_TYPE_TO_RIS = {
    "journalArticle": "JOUR", "book": "BOOK", "bookSection": "CHAP", "conferencePaper": "CPAPER", "thesis": "THES",
    "report": "RPRT", "webpage": "ELEC", "blogPost": "BLOG", "magazineArticle": "MGZN",
    "newspaperArticle": "NEWS", "encyclopediaArticle": "ENCYC", "dataset": "DATA", "computerProgram": "COMP",
    "patent": "PAT", "presentation": "SLIDE", "manuscript": "UNPB", "letter": "PCOMM", "videoRecording": "VIDEO",
    "document": "GEN", "preprint": "INPR",
}


def parse_ris(text: str) -> list[dict]:
    items: list[dict] = []
    rec: dict[str, list[str]] = {}
    last_tag = None
    for raw in (text or "").replace("\ufeff", "").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$", raw)
        if m:
            tag, value = m.group(1), m.group(2).strip()
            if tag == "ER":
                if rec:
                    items.append(_ris_record(rec))
                rec, last_tag = {}, None
                continue
            rec.setdefault(tag, []).append(value)
            last_tag = tag
        elif last_tag and raw.strip():
            rec[last_tag][-1] += " " + raw.strip()   # wrapped continuation line
    if rec:
        items.append(_ris_record(rec))
    return items


def _ris_record(r: dict[str, list[str]]) -> dict:
    g = lambda *tags: next((r[t][0] for t in tags if r.get(t) and r[t][0]), "")  # noqa: E731
    item_type = _RIS_TO_TYPE.get(g("TY").upper(), "document")
    creators = [catalog.parse_creator_string(a, "author") for t in ("AU", "A1") for a in r.get(t, [])]
    creators += [catalog.parse_creator_string(a, "editor") for t in ("ED", "A2") for a in r.get(t, [])
                 if item_type in ("bookSection", "conferencePaper", "book", "encyclopediaArticle") or t == "ED"]
    creators += [catalog.parse_creator_string(a, "translator") for a in r.get("A4", [])]
    date = g("DA", "PY", "Y1").strip("/")
    date = re.sub(r"^(\d{4})/(\d{2})/(\d{2}).*$", r"\1-\2-\3", date)
    date = re.sub(r"^(\d{4})/(\d{2})/?$", r"\1-\2", date)
    sp, ep = g("SP"), g("EP")
    pages = f"{sp}-{ep}" if sp and ep and sp != ep else sp
    container = g("T2", "JO", "JF", "JA", "BT") if item_type != "book" else ""
    doi = g("DO")
    if not doi:
        for u in r.get("UR", []) + r.get("M3", []):
            m = re.search(r"10\.\d{4,9}/\S+", u)
            if m:
                doi = m.group(0)
                break
    fields: dict[str, Any] = {}
    if item_type == "thesis" and g("M3"):
        fields["thesisType"] = g("M3")
    if item_type == "report" and g("IS"):
        fields["reportNumber"] = g("IS")
    if item_type == "conferencePaper" and g("T2") and g("C3"):
        fields["conferenceName"] = g("T2")
        container = g("C3")
    if g("J2"):
        fields["journalAbbreviation"] = g("J2")
    sn = g("SN")
    return {
        "item_type": item_type,
        "title": g("TI", "T1", "CT") or "Untitled",
        "creators": creators,
        "journal": container,
        "publisher": g("PB"),
        "place": g("CY", "PP"),
        "volume": g("VL"),
        "issue": g("IS") if item_type != "report" else "",
        "pages": pages,
        "edition": g("ET"),
        "series": g("T3"),
        "abstract": g("AB", "N2"),
        "doi": catalog.normalize_doi(doi) or None,
        "url": g("UR"),
        "issn": sn if item_type in ("journalArticle", "magazineArticle", "newspaperArticle") else "",
        "isbn": [sn] if sn and item_type in ("book", "bookSection", "encyclopediaArticle") else [],
        "language": g("LA"),
        "date": date,
        "year": catalog.year_from_date(date),
        "short_title": g("ST"),
        "extra": "\n".join(r.get("N1", [])),
        "access_date": g("Y2"),
        "fields": fields,
        "tags": [k for k in r.get("KW", []) if k],
        "_file": g("L1"),
    }


def export_ris(items: Iterable[dict]) -> str:
    lines: list[str] = []
    for it in items:
        t = catalog.normalize_type(it.get("item_type"))
        extra = it.get("fields") or {}
        rec: list[tuple[str, Any]] = [("TY", _TYPE_TO_RIS.get(t, "GEN")), ("TI", it.get("title"))]
        for c in it.get("creators") or catalog.clean_creators(it.get("authors") or []):
            tag = {"editor": "ED", "translator": "A4"}.get(c.get("creatorType"), "AU")
            rec.append((tag, catalog.creator_display(c)))
        if it.get("journal"):
            rec.append(("T2" if t != "journalArticle" else "JO", it["journal"]))
            if t == "journalArticle":
                rec.append(("T2", it["journal"]))
        rec += [("AB", it.get("abstract")), ("PB", it.get("publisher")), ("CY", it.get("place")),
                ("VL", it.get("volume")), ("IS", it.get("issue") or extra.get("reportNumber")),
                ("ET", it.get("edition")), ("T3", it.get("series")), ("DO", it.get("doi")), ("UR", it.get("url")),
                ("LA", it.get("language")), ("ST", it.get("short_title")), ("J2", extra.get("journalAbbreviation")),
                ("M3", extra.get("thesisType")), ("Y2", it.get("access_date")), ("N1", it.get("extra"))]
        pages = str(it.get("pages") or "")
        if pages:
            sp, _, ep = pages.partition("-")
            rec.append(("SP", sp.strip()))
            if ep:
                rec.append(("EP", ep.strip()))
        if it.get("year"):
            rec.append(("PY", str(it["year"])))
        if it.get("date"):
            d = str(it["date"])
            rec.append(("DA", d.replace("-", "/")))
        sn = it.get("issn") or ", ".join(it.get("isbn") or [])
        rec.append(("SN", sn))
        for tag in it.get("tags") or []:
            rec.append(("KW", tag["name"]))
        if it.get("file_path"):
            rec.append(("L1", "file:///" + str(it["file_path"]).replace("\\", "/")))
        for tag, value in rec:
            if value not in (None, ""):
                lines.append(f"{tag}  - {value}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


# ======================================================================================
# CSL-JSON, CSV
# ======================================================================================

def export_csljson(items: Iterable[dict]) -> str:
    return json.dumps([item_to_csl(it) for it in items], ensure_ascii=False, indent=2)


def export_csv(items: Iterable[dict]) -> str:
    out = io.StringIO()
    cols = ["Key", "Item Type", "Publication Year", "Author", "Title", "Publication Title", "ISBN", "ISSN", "DOI",
            "Url", "Abstract Note", "Date", "Date Added", "Date Modified", "Pages", "Issue", "Volume",
            "Publisher", "Place", "Language", "Extra", "Manual Tags", "Notes", "File Attachments", "Citation Key"]
    w = csv.writer(out)
    w.writerow(cols)
    for it in items:
        creators = it.get("creators") or catalog.clean_creators(it.get("authors") or [])
        w.writerow([
            it.get("id"), it.get("item_type"), it.get("year") or "",
            "; ".join(catalog.creator_display(c) for c in creators), it.get("title") or "", it.get("journal") or "",
            ", ".join(it.get("isbn") or []), it.get("issn") or "", it.get("doi") or "", it.get("url") or "",
            it.get("abstract") or "", it.get("date") or "", it.get("added_at") or "", it.get("updated_at") or "",
            it.get("pages") or "", it.get("issue") or "", it.get("volume") or "", it.get("publisher") or "",
            it.get("place") or "", it.get("language") or "", it.get("extra") or "",
            "; ".join(t["name"] for t in it.get("tags") or []),
            " | ".join(n.get("title", "") for n in it.get("notes_list") or []),
            it.get("file_path") or "", it.get("citation_key") or "",
        ])
    return out.getvalue()


# ======================================================================================
# Microsoft Word bibliography sources (References > Manage Sources)
# ======================================================================================

_TYPE_TO_WORD = {
    "journalArticle": "JournalArticle", "magazineArticle": "ArticleInAPeriodical",
    "newspaperArticle": "ArticleInAPeriodical", "book": "Book", "bookSection": "BookSection",
    "conferencePaper": "ConferenceProceedings", "report": "Report", "thesis": "Report", "webpage": "InternetSite",
    "blogPost": "InternetSite", "patent": "Patent", "videoRecording": "Film", "computerProgram": "ElectronicSource",
    "dataset": "ElectronicSource", "preprint": "Report",
}


def export_word_xml(items: Iterable[dict]) -> str:
    ns = "http://schemas.openxmlformats.org/officeDocument/2006/bibliography"
    parts = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<b:Sources SelectedStyle="" xmlns:b="{ns}" '
             f'xmlns="{ns}">']
    for it in items:
        t = catalog.normalize_type(it.get("item_type"))
        e = lambda v: xml_escape(str(v))  # noqa: E731
        src = [f"<b:Tag>{e(it.get('citation_key') or 'item' + str(it.get('id')))}</b:Tag>",
               f"<b:SourceType>{_TYPE_TO_WORD.get(t, 'Misc')}</b:SourceType>",
               f"<b:Title>{e(it.get('title') or '')}</b:Title>"]
        creators = it.get("creators") or catalog.clean_creators(it.get("authors") or [])
        # Word allows either ONE <b:Corporate> or a <b:NameList> per role. A lone organisation uses
        # Corporate; organisations mixed with people become a Person with only <b:Last> (no data loss).
        roles: dict[str, list[dict]] = {}
        for c in creators:
            role = {"editor": "Editor", "translator": "Translator"}.get(c.get("creatorType"), "Author")
            roles.setdefault(role, []).append(c)
        if roles:
            src.append("<b:Author>")
            for role, people in roles.items():
                src.append(f"<b:{role}>")
                if len(people) == 1 and people[0].get("name"):
                    src.append(f"<b:Corporate>{e(people[0]['name'])}</b:Corporate>")
                else:
                    persons = []
                    for p in people:
                        if p.get("name"):
                            persons.append(f"<b:Person><b:Last>{e(p['name'])}</b:Last></b:Person>")
                        else:
                            persons.append(f"<b:Person><b:Last>{e(p.get('lastName') or '')}</b:Last>"
                                           f"<b:First>{e(p.get('firstName') or '')}</b:First></b:Person>")
                    src.append("<b:NameList>" + "".join(persons) + "</b:NameList>")
                src.append(f"</b:{role}>")
            src.append("</b:Author>")
        container_tag = {"journalArticle": "JournalName", "magazineArticle": "PeriodicalTitle",
                         "newspaperArticle": "PeriodicalTitle", "bookSection": "BookTitle",
                         "conferencePaper": "ConferenceName", "webpage": "InternetSiteTitle",
                         "blogPost": "InternetSiteTitle"}.get(t)
        values = {
            container_tag: it.get("journal") if container_tag else None,
            "Year": it.get("year"), "Pages": it.get("pages"), "Volume": it.get("volume"), "Issue": it.get("issue"),
            "Publisher": it.get("publisher") if t not in ("report", "thesis") else None,
            "Institution": it.get("publisher") if t in ("report", "thesis") else None,
            "City": it.get("place"), "URL": it.get("url"), "DOI": it.get("doi"), "Edition": it.get("edition"),
            "StandardNumber": it.get("issn") or ", ".join(it.get("isbn") or []),
            "ThesisType": (it.get("fields") or {}).get("thesisType") if t == "thesis" else None,
            "Comments": it.get("extra"), "LCID": None,
        }
        m = re.match(r"^\d{4}-(\d{2})(?:-(\d{2}))?", str(it.get("date") or ""))
        if m:
            values["Month"] = m.group(1)
            if m.group(2):
                values["Day"] = m.group(2)
        for tag, value in values.items():
            if tag and value not in (None, ""):
                src.append(f"<b:{tag}>{e(value)}</b:{tag}>")
        parts.append("<b:Source>" + "".join(src) + "</b:Source>")
    parts.append("</b:Sources>")
    return "\n".join(parts)


EXPORTERS = {
    "bibtex": (lambda items: export_bibtex(items), "application/x-bibtex", "bib"),
    "biblatex": (lambda items: export_bibtex(items, biblatex=True), "application/x-bibtex", "bib"),
    "ris": (export_ris, "application/x-research-info-systems", "ris"),
    "csljson": (export_csljson, "application/vnd.citationstyles.csl+json", "json"),
    "csv": (export_csv, "text/csv", "csv"),
    "wordxml": (export_word_xml, "application/xml", "xml"),
}
