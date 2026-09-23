"""Bounded, offline parsing of publication lists and bibliographic files.

Entries are lookup proposals, not resolved references. Supplied structured fields
are preserved using catalog payload names; plain citations remain search queries.
No parser opens the library, performs a network request, or extracts archive files.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from . import catalog, formats

# Bounds apply before parsing, decompression, and lookup-job construction.
MAX_RECORDS = 500
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_RECORD_CHARACTERS = 16_000
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2048
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s<>\"\u201c\u201d]+", re.I)
_NUMBER = re.compile(r"^\s*(?:\[\d{1,4}\]|\d{1,4}[.)])\s+")
_BULLET = re.compile(r"^\s*[-*\u2022]\s+")
_SECTION = re.compile(r"^(?:references|bibliography|literature cited)\s*:?$", re.I)
_HEADERS = {
    "doi": "doi", "digitalobjectidentifier": "doi", "title": "title", "articletitle": "title",
    "authors": "authors", "author": "authors", "year": "year", "publicationyear": "year",
    "journal": "journal", "publicationtitle": "journal", "containertitle": "journal",
    "url": "url", "link": "url", "abstract": "abstract", "date": "date",
    "publisher": "publisher", "volume": "volume", "issue": "issue", "pages": "pages",
    "pmid": "pmid", "isbn": "isbn",
}
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _result(fmt: str = "text") -> dict:
    """Use one serializable result shape for successful and rejected inputs."""
    return {"entries": [], "warnings": [], "errors": [], "format": fmt}


def _dois(text: str) -> list[str]:
    """Trim citation punctuation while retaining balanced DOI parentheses."""
    values = []
    for match in _DOI.finditer(text):
        value = match.group().rstrip(".,;:")
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
            while value.endswith(closing) and value.count(closing) > value.count(opening):
                value = value[:-1]
        value = value.rstrip(".,;:").lower()
        if value not in values:
            values.append(value)
    return values


def _add(result: dict, query: str, metadata: dict, source: str) -> None:
    """Validate each proposal and prefer one explicit DOI over its citation text."""
    result["_records"] = result.get("_records", 0) + 1
    if result["_records"] > MAX_RECORDS:
        raise ValueError(f"Maximum {MAX_RECORDS} publications per import. Split this batch into smaller files.")
    if metadata.get("doi") and not _dois(str(metadata["doi"])):
        result["warnings"].append(f"{source}: invalid DOI; using another supplied identifier or title.")
        invalid = metadata["doi"]
        metadata = {key: value for key, value in metadata.items() if key != "doi"}
        if query == str(invalid):
            query = str(metadata.get("title") or metadata.get("url") or "")
    query = re.sub(r"\s+", " ", query).strip()
    if not query or not re.search(r"\w", query):
        result["warnings"].append(f"{source}: no usable identifier or title; entry not imported.")
        return
    if len(query) > MAX_RECORD_CHARACTERS:
        result["errors"].append(f"{source}: reference exceeds {MAX_RECORD_CHARACTERS:,} characters.")
        return
    identifiers = _dois(str(metadata.get("doi") or query))
    if len(identifiers) > 1:
        result["warnings"].append(f"{source}: multiple DOIs found; split this entry into separate references before import.")
        return
    if identifiers:
        metadata = {**metadata, "doi": identifiers[0]}
        query = identifiers[0]
    result["entries"].append({"query": query, "metadata": metadata, "source": source})


def _finish(result: dict) -> dict:
    """Reject oversized batches as a whole instead of silently truncating them."""
    result.pop("_records", None)
    if len(result["entries"]) > MAX_RECORDS:
        result["errors"].append(f"Maximum {MAX_RECORDS} publications per import. Split this batch into smaller files.")
    if result["errors"]:
        result["entries"] = []
    elif not result["entries"]:
        result["errors"].append("No usable publications found. Supply DOIs, titles, or structured references.")
    return result


def _decode(data: bytes) -> str:
    """Accept Unicode text without replacing undecodable bytes or guessing binary."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if b"\x00" in data:
        # UTF-16 without a BOM is common in exported Windows tables. Require a
        # convincing alternating-null pattern before trying its byte order.
        sample = data[:2000]
        even = sample[::2].count(0) / max(1, len(sample[::2]))
        odd = sample[1::2].count(0) / max(1, len(sample[1::2]))
        if max(even, odd) < 0.3 or abs(even - odd) < 0.2:
            raise ValueError("File contains binary data. Export it as UTF-8 text or CSV.")
        return data.decode("utf-16-le" if odd > even else "utf-16-be")
    try:
        return data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Text encoding is unsupported. Save the file as UTF-8 or UTF-16 and retry.") from exc


def _plain_entries(text: str, result: dict, prefix: str = "Text") -> None:
    """Keep line lists separate and join only explicit or indented continuations."""
    lines = text.splitlines()
    numbered = any(_NUMBER.match(line) for line in lines)
    current, start = [], 0

    def flush() -> None:
        if current:
            _add(result, " ".join(current), {}, f"{prefix} line {start}")
            current.clear()

    for number, line in enumerate(lines, 1):
        if not line.strip():
            flush()
            continue
        if _SECTION.fullmatch(line.strip()):
            flush()
            result["warnings"].append(f"{prefix} line {number}: bibliography heading skipped.")
            continue
        marker = _NUMBER.match(line) or _BULLET.match(line)
        value = line[marker.end():] if marker else line.strip()
        if marker or not current:
            flush()
            start = number
            current.append(value)
        elif numbered or line[:1].isspace():
            # A standalone DOI list is still one entry per DOI.
            if _dois(" ".join(current)) and _dois(value):
                flush()
                start = number
            current.append(value)
        else:
            flush()
            start = number
            current.append(value)
    flush()
    if not numbered and len(lines) > 1:
        result["warnings"].append("Unnumbered text uses one reference per line; indent wrapped continuation lines or number references to keep them together.")


def _header(value: str) -> str | None:
    return _HEADERS.get(re.sub(r"[^a-z]", "", value.lower().lstrip("\ufeff")))


def _delimiter(text: str) -> str:
    """Prefer recognized column names, then use bounded dialect detection."""
    first = text.splitlines()[0] if text.splitlines() else ""
    scores = {delimiter: sum(_header(cell) is not None for cell in next(csv.reader([first], delimiter=delimiter)))
              for delimiter in (",", ";", "\t")}
    best = max(scores, key=scores.get)
    if scores[best] >= 2:
        return best
    try:
        return csv.Sniffer().sniff(text[:16_384], delimiters=",;\t").delimiter
    except csv.Error:
        return "\t" if "\t" in first else ","


def _csv_rows(rows: list[list[str]], result: dict, prefix: str) -> None:
    """Map explicit headers; headerless records keep their complete search text."""
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return
    headers = [_header(cell) for cell in rows[0]]
    has_header = any(field in ("doi", "title", "pmid", "isbn") for field in headers)
    if len(rows) - int(has_header) > MAX_RECORDS:
        raise ValueError(f"Maximum {MAX_RECORDS} publications per import. Split this batch into smaller files.")
    if has_header:
        if len([field for field in headers if field]) != len(set(field for field in headers if field)):
            raise ValueError(f"{prefix}: duplicate recognized column names; give each field one column.")
        unknown = [cell for cell, field in zip(rows[0], headers) if not field and cell.strip()]
        if unknown:
            result["warnings"].append(f"{prefix}: unsupported columns ignored: {', '.join(unknown)}.")
    for number, row in enumerate(rows[1:] if has_header else rows, 2 if has_header else 1):
        source = f"{prefix} row {number}"
        if has_header and len(row) != len(headers):
            result["warnings"].append(f"{source}: expected {len(headers)} columns, found {len(row)}; fix the CSV quoting before import.")
            continue
        if not has_header:
            nonempty = [cell.strip() for cell in row if cell.strip()]
            identifiers = [cell for cell in nonempty if len(_dois(cell)) == 1 and re.match(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:|10\.)", cell, re.I)]
            if len(nonempty) == 2 and len(identifiers) == 1:
                title = next(cell for cell in nonempty if cell != identifiers[0])
                _add(result, identifiers[0], {"title": title}, source)
            else:
                _add(result, ", ".join(nonempty), {}, source)
            continue
        metadata = {field: cell.strip() for field, cell in zip(headers, row) if field and cell.strip()}
        if "authors" in metadata:
            names = re.split(r"\s*;\s*|\s+and\s+|\s*\|\s*", metadata.pop("authors"), flags=re.I)
            metadata["creators"] = [catalog.parse_creator_string(name) for name in names if name.strip()]
        if "year" in metadata:
            year = metadata["year"]
            if not re.fullmatch(r"\d{4}", year):
                result["warnings"].append(f"{source}: invalid year '{year}' retained as date text.")
                metadata.setdefault("date", year)
                del metadata["year"]
            else:
                metadata["year"] = int(year)
        if metadata.get("doi") and len(_dois(metadata["doi"])) != 1:
            result["warnings"].append(f"{source}: invalid DOI; using another supplied identifier or title.")
            del metadata["doi"]
        if "isbn" in metadata:
            metadata["isbn"] = [metadata["isbn"]]
        query = str(metadata.get("doi") or metadata.get("title") or metadata.get("url") or
                    ("PMID:" + metadata["pmid"] if metadata.get("pmid") else "") or
                    (metadata.get("isbn") or [""])[0])
        _add(result, query, metadata, source)


def _docx(data: bytes, result: dict) -> None:
    """Read body paragraphs and table rows in order without extracting files."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(members) > MAX_ARCHIVE_MEMBERS or len(set(names)) != len(names):
            raise ValueError("DOCX contains too many or duplicate archive members.")
        if sum(member.file_size for member in members) > MAX_ARCHIVE_BYTES:
            raise ValueError("DOCX expanded contents exceed the 20 MB limit.")
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or ":" in member.filename or member.flag_bits & 1:
                raise ValueError("DOCX contains unsafe paths or encrypted archive members.")
            if member.file_size > 1024 * 1024 and member.file_size > max(1, member.compress_size) * 200:
                raise ValueError("DOCX compression ratio exceeds the safe archive limit.")
        if "word/document.xml" not in names:
            raise ValueError("This file is not a valid DOCX document: word/document.xml is missing.")
        if any(re.match(r"word/(?:footnotes|endnotes|header\d*|footer\d*)\.xml$", name) for name in names):
            result["warnings"].append("DOCX imports the main document body only; headers, footers, footnotes and endnotes are excluded.")
        with archive.open("word/document.xml") as document:
            xml = document.read(MAX_XML_BYTES + 1)
        if len(xml) > MAX_XML_BYTES:
            raise ValueError("DOCX document XML exceeds the 4 MB limit.")
    decoded = _decode(xml)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", decoded, re.I):
        raise ValueError("DOCX XML declarations with entities or a DTD are unsupported.")
    root = ElementTree.fromstring(decoded)
    body = root.find(_WORD + "body")
    if body is None:
        raise ValueError("DOCX has no readable document body.")

    def paragraph(node) -> str:
        return "".join((part.text or "") if part.tag == _WORD + "t" else " "
                       for part in node.iter() if part.tag in (_WORD + "t", _WORD + "tab", _WORD + "br"))

    def blocks(container):
        for node in container:
            if node.tag in (_WORD + "p", _WORD + "tbl"):
                yield node
            elif node.tag in (_WORD + "sdt", _WORD + "sdtContent", _WORD + "customXml"):
                yield from blocks(node)

    for number, node in enumerate(blocks(body), 1):
        if node.tag == _WORD + "p":
            text = paragraph(node).strip()
            if _SECTION.fullmatch(text):
                result["warnings"].append(f"DOCX paragraph {number}: bibliography heading skipped.")
            elif text:
                _add(result, _NUMBER.sub("", _BULLET.sub("", text)), {}, f"DOCX paragraph {number}")
        elif node.tag == _WORD + "tbl":
            if any(nested is not node for nested in node.iter(_WORD + "tbl")):
                raise ValueError(f"DOCX table {number}: nested tables are unsupported; flatten them to ordinary rows before import.")
            rows = [[" ".join(paragraph(p) for p in cell.findall(_WORD + "p")).strip()
                     for cell in row.findall(_WORD + "tc")] for row in node.findall(_WORD + "tr")]
            _csv_rows(rows, result, f"DOCX table {number}")


def _validate_csl(records: list[dict], result: dict) -> None:
    """Reject ill-typed fields that the permissive legacy converter would omit."""
    for number, record in enumerate(records, 1):
        prefix = f"CSL-JSON reference {number}"
        for field in ("DOI", "URL", "PMID"):
            if field in record and record[field] is not None and not isinstance(record[field], str):
                raise ValueError(f"{prefix}: {field} must be text.")
        title = record.get("title")
        if title is not None and not isinstance(title, str):
            if not isinstance(title, list) or any(not isinstance(value, str) for value in title):
                raise ValueError(f"{prefix}: title must be text or a list of title strings.")
            if len(title) > 1:
                result["warnings"].append(f"{prefix}: multiple titles supplied; the existing CSL converter uses the first title.")
        for field in ("author", "editor", "translator"):
            people = record.get(field)
            if people is not None and (not isinstance(people, list) or any(not isinstance(person, dict) for person in people)):
                raise ValueError(f"{prefix}: {field} must be a list of name objects.")
        for field in ("issued", "accessed", "event-date", "original-date"):
            if record.get(field) is not None and not isinstance(record[field], dict):
                raise ValueError(f"{prefix}: {field} must be a CSL date object.")


def parse_bulk_source(text: str = "", *, filename: str = "", data: bytes | None = None) -> dict:
    """Normalize one source with explicit warnings and atomic hard-limit errors."""
    result = _result()
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".doc":
            raise ValueError("Legacy Word .doc files are unsupported. Open the file in Word and Save As .docx, then import it again.")
        if data is not None and len(data) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the 10 MB upload limit.")
        if suffix == ".docx":
            result["format"] = "docx"
            if data is None:
                raise ValueError("DOCX requires the file bytes, not pasted binary content.")
            _docx(data, result)
            return _finish(result)
        if suffix not in ("", ".txt", ".csv", ".tsv", ".bib", ".bibtex", ".biblatex", ".ris", ".enw", ".json"):
            raise ValueError("Unsupported file type. Use TXT, CSV, TSV, DOCX, BibTeX, RIS or CSL-JSON.")
        text = _decode(data) if data is not None else text.lstrip("\ufeff")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("Text exceeds the 2 MB limit.")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
            raise ValueError("Text contains binary control characters. Export it as plain Unicode text.")
        fmt = formats.detect_format(text, filename)
        # Numbered references such as '[1] Author...' are not JSON arrays.
        if fmt == "csljson" and suffix != ".json" and re.match(r"^\[[^\]\r\n]+\]\s+\S", text.lstrip()):
            fmt = "identifiers"
        if fmt in ("bibtex", "ris", "csljson"):
            result["format"] = fmt
            if fmt == "csljson":
                payload = json.loads(text)
                records = payload.get("items", [payload]) if isinstance(payload, dict) else payload
                if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                    raise ValueError("CSL-JSON must contain reference objects; non-reference values were found.")
                _validate_csl(records, result)
            parsed = formats.parse(text, fmt=fmt, filename=filename)
            expected = (len(re.findall(r"^TY\s{2}-", text, re.M)) if fmt == "ris" else
                        len(re.findall(r"@(?!comment\b|string\b|preamble\b)\w+\s*[{(]", text, re.I)) if fmt == "bibtex" else len(records))
            if expected != len(parsed):
                raise ValueError(f"{fmt}: {expected} references detected but only {len(parsed)} parsed. Repair malformed records before import.")
            for number, metadata in enumerate(parsed, 1):
                query = metadata.get("doi") or metadata.get("title") or metadata.get("url") or ""
                if query == "Untitled" and not metadata.get("doi"):
                    query = ""
                _add(result, str(query), metadata, f"{fmt} reference {number}")
        else:
            delimiter = _delimiter(text)
            first = next(csv.reader(io.StringIO(text), delimiter=delimiter), [])
            csv_like = any(_header(cell) in ("doi", "title", "pmid", "isbn") for cell in first)
            if suffix in (".csv", ".tsv") or csv_like:
                result["format"] = "csv"
                _csv_rows(list(csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else delimiter, strict=True)), result, "CSV")
            else:
                _plain_entries(text, result)
    except (ValueError, UnicodeError, csv.Error, zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, ElementTree.ParseError) as exc:
        result["errors"].append(str(exc) or "The source could not be parsed.")
    return _finish(result)


def parse_text(text: str) -> dict:
    """Convenience entry point for pasted publication lists."""
    return parse_bulk_source(text)


def parse_file(path: str | Path) -> dict:
    """Read at most the upload bound from a caller-selected file; never modify it."""
    path = Path(path)
    try:
        with path.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
        return parse_bulk_source(filename=path.name, data=data)
    except OSError as exc:
        result = _result()
        result["errors"].append(f"Cannot read file: {exc}")
        return result
