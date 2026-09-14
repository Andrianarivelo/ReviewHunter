/**
 * COinS reader (ContextObjects in Spans, class="Z3988"): OpenURL KEV strings in
 * the title attribute of empty spans, used by WordPress plugins, library
 * catalogues, Wikipedia citation templates and many repositories.
 *
 * @module extract/coins
 */

import { clean, normalizeDate, pageRange, parseCreator, cleanIsbns } from "./normalize.js";
import { cleanDoi, arxivIdFrom } from "../shared/identifiers.js";

/**
 * Parse an OpenURL KEV string into a multi-map.
 *
 * @param {string} kev  "ctx_ver=Z39.88-2004&rft.atitle=...".
 * @returns {Map<string, string[]>} Keys to values (decoded, "+" as space).
 */
export function parseKev(kev) {
  const map = new Map();
  for (const pair of String(kev || "").split(/&(?:amp;)?/)) {
    const idx = pair.indexOf("=");
    if (idx <= 0) continue;
    const key = decode(pair.slice(0, idx));
    const value = clean(decode(pair.slice(idx + 1)));
    if (!value) continue;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(value);
  }
  return map;
}

/**
 * URL-decode a KEV component ("+" means space) without throwing.
 *
 * @param {string} s  Encoded component.
 * @returns {string} Decoded text.
 */
function decode(s) {
  try {
    return decodeURIComponent(s.replace(/\+/g, " "));
  } catch (_) {
    return s.replace(/\+/g, " ");
  }
}

/**
 * Convert one KEV context object into a partial payload.
 *
 * @param {Map<string, string[]>} kv  Parsed KEV.
 * @returns {object} Partial payload (title may be empty when the span is incomplete).
 */
export function kevToPayload(kv) {
  const g = (k) => (kv.get(k) || [])[0] || "";
  const fmt = g("rft_val_fmt").toLowerCase();
  const genre = g("rft.genre").toLowerCase();
  let item_type = "journalArticle";
  if (fmt.includes(":book")) {
    item_type = genre === "bookitem" || g("rft.atitle") ? "bookSection" : genre === "report" ? "report" : genre === "proceeding" || genre === "conference" ? "conferencePaper" : "book";
  } else if (fmt.includes(":dissertation")) item_type = "thesis";
  else if (fmt.includes(":patent")) item_type = "patent";
  else if (fmt.includes(":dc")) item_type = "webpage";
  else if (genre === "proceeding" || genre === "conference") item_type = "conferencePaper";
  else if (genre === "preprint") item_type = "preprint";

  const creators = [];
  for (const au of kv.get("rft.au") || []) {
    const c = parseCreator(au);
    if (c) creators.push(c);
  }
  if (!creators.length && (g("rft.aulast") || g("rft.aucorp"))) {
    creators.push(g("rft.aucorp") ? { creatorType: "author", name: g("rft.aucorp") } : { creatorType: "author", lastName: g("rft.aulast"), firstName: g("rft.aufirst") || g("rft.auinit") });
  }
  let doi = "";
  let url = "";
  let pmid = "";
  let arxiv = "";
  for (const id of [...(kv.get("rft_id") || []), ...(kv.get("rft.identifier") || [])]) {
    if (/^info:doi\//i.test(id) || /doi\.org\//i.test(id)) doi = cleanDoi(id.replace(/^info:doi\//i, ""));
    else if (/^info:pmid\//i.test(id)) pmid = id.replace(/^info:pmid\//i, "");
    else if (/arxiv/i.test(id)) arxiv = arxivIdFrom(id);
    else if (/^https?:\/\//i.test(id) && !url) url = id;
  }
  const title = item_type === "book" ? g("rft.btitle") || g("rft.title") : g("rft.atitle") || g("rft.title") || g("rft.btitle");
  // Container title: book title for chapters, journal title otherwise (rft.title doubles as journal in KEV 0.1).
  let journal = "";
  if (item_type === "bookSection") journal = g("rft.btitle");
  else if (item_type !== "book") journal = g("rft.jtitle") || g("rft.stitle") || (g("rft.title") !== title ? g("rft.title") : "");
  return {
    item_type,
    title,
    creators,
    journal,
    date: normalizeDate(g("rft.date")),
    volume: g("rft.volume"),
    issue: g("rft.issue"),
    pages: g("rft.pages") || pageRange(g("rft.spage"), g("rft.epage")),
    issn: g("rft.issn") || g("rft.eissn"),
    isbn: cleanIsbns(kv.get("rft.isbn") || []),
    publisher: g("rft.pub") || g("rft.inst") || g("rft.publisher"),
    place: g("rft.place"),
    language: g("rft.language"),
    doi,
    pmid,
    arxiv_id: arxiv,
    url,
  };
}

/**
 * Read every COinS span in the document.
 *
 * @param {Document} doc  Document.
 * @returns {object[]} Partial payloads, only those with a title or a DOI.
 */
export function readCoins(doc) {
  const out = [];
  for (const span of doc.querySelectorAll("span.Z3988, span[class~='Z3988']")) {
    const payload = kevToPayload(parseKev(span.getAttribute("title")));
    if (payload.title || payload.doi) out.push(payload);
  }
  return out;
}
