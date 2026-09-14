/**
 * Normalisation helpers turning scraped strings into ReviewHunter payload values
 * (creators, dates, pages, item types). Pure functions, no DOM access.
 *
 * @module extract/normalize
 */

/** Words that mark a creator string as an organisation (kept as a single name). */
const ORG_WORDS = /\b(consortium|group|collaboration|committee|association|society|institute|university|organi[sz]ation|council|agency|foundation|team|network|centre|center|department|ministry|office|world health|contributors)\b/i;

/** Name particles that belong to the family name ("van der Waals", "de Gennes"). */
const PARTICLES = new Set(["van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "du", "la", "le", "st.", "ten", "ter", "bin", "al", "el"]);

/**
 * Collapse whitespace and trim. Also strips zero-width characters.
 *
 * @param {*} s  Any value.
 * @returns {string} Clean single-line string ("" for null/undefined).
 */
export function clean(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[​-‍﻿]/g, "").replace(/\s+/g, " ").trim();
}

/**
 * Strip HTML tags from a string (abstracts often contain <p>, <i>, <sub>).
 *
 * @param {string} s  HTML fragment.
 * @returns {string} Plain text.
 */
export function stripTags(s) {
  return clean(String(s || "").replace(/<\/(p|div|br|li|h\d)>/gi, " ").replace(/<[^>]+>/g, " "))
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"');
}

/**
 * Convert "ALL CAPS TITLES" to Title Case; mixed-case strings are returned unchanged.
 *
 * @param {string} s  Name or title.
 * @returns {string} Possibly re-cased string.
 */
export function fixAllCaps(s) {
  const t = clean(s);
  if (t.length > 3 && t === t.toUpperCase() && /[A-Z]/.test(t)) {
    return t.toLowerCase().replace(/(^|[\s\-'(])(\p{L})/gu, (m, p, c) => p + c.toUpperCase());
  }
  return t;
}

/**
 * Parse one personal or corporate name into a ReviewHunter creator.
 * Handles "Last, First", "First Middle Last", particles and organisations.
 *
 * @param {string} raw  Name as written on the page.
 * @param {string} [creatorType="author"]  Zotero creator type.
 * @returns {?{creatorType: string, lastName?: string, firstName?: string, name?: string}}
 *          Creator object, or null for empty input.
 */
export function parseCreator(raw, creatorType = "author") {
  let s = clean(raw).replace(/^(by|par|von)\s+/i, "").replace(/\s*\(.*?\)\s*$/, "").replace(/[\d*†‡§¶#]+$/, "").trim();
  if (!s || s.length > 200) return null;
  if (ORG_WORDS.test(s) || /^[^\s,]+$/.test(s) && s.length > 1 && !/\./.test(s) && s === s.toUpperCase() && s.length > 5) {
    return { creatorType, name: s };
  }
  if (s.includes(",")) {
    const [last, ...rest] = s.split(",");
    const first = clean(rest.join(" ").replace(/\b(jr|sr|ii|iii|iv)\.?\b/i, ""));
    if (!first) return /\s/.test(last) ? parseCreator(last, creatorType) : { creatorType, name: clean(last) };
    return { creatorType, lastName: fixAllCaps(last), firstName: fixAllCaps(first) };
  }
  const parts = s.split(" ");
  if (parts.length === 1) return { creatorType, lastName: fixAllCaps(parts[0]), firstName: "" };
  // PubMed style "Auger N" (family name then initials).
  if (parts.length >= 2 && /^[A-Z]{1,3}$/.test(parts[parts.length - 1]) && parts[0].length > 1 && !/^[A-Z]{1,3}$/.test(parts[0])) {
    return { creatorType, lastName: parts.slice(0, -1).join(" "), firstName: parts[parts.length - 1] };
  }
  let i = parts.length - 1;
  while (i > 1 && PARTICLES.has(parts[i - 1].toLowerCase())) i -= 1;
  return { creatorType, lastName: fixAllCaps(parts.slice(i).join(" ")), firstName: fixAllCaps(parts.slice(0, i).join(" ")) };
}

/**
 * Parse a list of names that may be separated by ";" or " and " inside one string.
 *
 * @param {string[]|string} values  One or several author strings.
 * @param {string} [creatorType="author"]  Zotero creator type.
 * @returns {object[]} Creators, duplicates removed.
 */
export function parseCreators(values, creatorType = "author") {
  const list = Array.isArray(values) ? values : [values];
  const out = [];
  const seen = new Set();
  for (const v of list) {
    const s = clean(v);
    if (!s) continue;
    const pieces = s.includes(";") ? s.split(";") : (/\s+and\s+/i.test(s) && !s.includes(",") ? s.split(/\s+and\s+/i) : [s]);
    for (const p of pieces) {
      const c = parseCreator(p, creatorType);
      if (!c) continue;
      const key = (c.name || `${c.lastName}|${c.firstName}`).toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        out.push(c);
      }
    }
  }
  return out;
}

/** English month names/abbreviations to month numbers. */
const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, sept: 9, oct: 10, nov: 11, dec: 12 };

/**
 * Normalise a date to "YYYY-MM-DD", "YYYY-MM" or "YYYY" when recognisable;
 * otherwise return the cleaned free-text date (the server accepts free dates).
 *
 * @param {string} raw  Date as found on the page ("2017/05/12", "12 May 2017", ISO timestamps...).
 * @returns {string} Normalised date or "".
 */
export function normalizeDate(raw) {
  const s = clean(raw);
  if (!s) return "";
  const pad = (n) => String(n).padStart(2, "0");
  let m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (m) return `${m[1]}-${pad(m[2])}-${pad(m[3])}`;
  m = s.match(/^(\d{4})[-/.](\d{1,2})$/);
  if (m) return `${m[1]}-${pad(m[2])}`;
  m = s.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/);
  if (m) return `${m[3]}-${pad(m[2])}-${pad(m[1])}`;
  m = s.match(/^(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})/);
  if (m && MONTHS[m[2].slice(0, 3).toLowerCase()]) return `${m[3]}-${pad(MONTHS[m[2].slice(0, 3).toLowerCase()])}-${pad(m[1])}`;
  m = s.match(/^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})/);
  if (m && MONTHS[m[1].slice(0, 3).toLowerCase()]) return `${m[3]}-${pad(MONTHS[m[1].slice(0, 3).toLowerCase()])}-${pad(m[2])}`;
  m = s.match(/^(\d{4})\s+([A-Za-z]{3,9})(?:\s+(\d{1,2}))?/);
  if (m && MONTHS[m[2].slice(0, 3).toLowerCase()]) {
    return m[3] ? `${m[1]}-${pad(MONTHS[m[2].slice(0, 3).toLowerCase()])}-${pad(m[3])}` : `${m[1]}-${pad(MONTHS[m[2].slice(0, 3).toLowerCase()])}`;
  }
  m = s.match(/^([A-Za-z]{3,9})\.?\s+(\d{4})$/);
  if (m && MONTHS[m[1].slice(0, 3).toLowerCase()]) return `${m[2]}-${pad(MONTHS[m[1].slice(0, 3).toLowerCase()])}`;
  if (/^\d{4}$/.test(s)) return s;
  return s;
}

/**
 * Extract a 4-digit year from any date string.
 *
 * @param {string} date  Date string.
 * @returns {string} Year or "".
 */
export function yearOf(date) {
  const m = String(date || "").match(/\b(1[5-9]\d{2}|20\d{2})\b/);
  return m ? m[1] : "";
}

/**
 * Build a page range from first/last page values.
 *
 * @param {string} first  First page (may already be a range).
 * @param {string} last   Last page.
 * @returns {string} "first-last", "first", or "".
 */
export function pageRange(first, last) {
  const f = clean(first);
  const l = clean(last);
  if (f && l && f !== l) return `${f}-${l}`;
  return f || l;
}

/**
 * Split a keyword string ("a; b, c") or array into distinct tags.
 *
 * @param {string[]|string} values  Keywords.
 * @returns {string[]} Tags (max 30, 1 to 80 chars each).
 */
export function splitKeywords(values) {
  const list = Array.isArray(values) ? values : [values];
  const out = [];
  for (const v of list) {
    for (const k of String(v || "").split(/[;,|]/)) {
      const t = clean(k);
      if (t && t.length <= 80 && !out.some((x) => x.toLowerCase() === t.toLowerCase())) out.push(t);
    }
  }
  return out.slice(0, 30);
}

/**
 * Keep only ISBN-looking values, digits and X only.
 *
 * @param {string[]|string} values  Raw ISBN strings.
 * @returns {string[]} Distinct ISBNs.
 */
export function cleanIsbns(values) {
  const list = Array.isArray(values) ? values : [values];
  const out = [];
  for (const v of list) {
    // A run of digits / hyphens / spaces starting with a digit, ending with a digit or X.
    for (const m of String(v || "").matchAll(/(?<![\dX])(\d[\d\- ]{8,15}[\dXx])(?![\dX])/g)) {
      const isbn = m[1].replace(/[\s-]/g, "").toUpperCase();
      if ((isbn.length === 10 || isbn.length === 13) && !out.includes(isbn)) out.push(isbn);
    }
  }
  return out;
}

/** schema.org @type to Zotero item type. */
const SCHEMA_TYPES = {
  scholarlyarticle: "journalArticle",
  medicalscholarlyarticle: "journalArticle",
  article: "webpage",
  newsarticle: "newspaperArticle",
  reportagenewsarticle: "newspaperArticle",
  blogposting: "blogPost",
  socialmediaposting: "forumPost",
  book: "book",
  chapter: "bookSection",
  dataset: "dataset",
  softwaresourcecode: "computerProgram",
  softwareapplication: "computerProgram",
  thesis: "thesis",
  report: "report",
  videoobject: "videoRecording",
  audioobject: "audioRecording",
  podcastepisode: "podcast",
  webpage: "webpage",
  itempage: "webpage",
  collectionpage: "webpage",
  techarticle: "report",
  presentationdigitaldocument: "presentation",
  map: "map",
  movie: "film",
  legislation: "statute",
  patent: "patent",
};

/**
 * Map a schema.org type (string or array) to a Zotero item type.
 *
 * @param {string|string[]} type  JSON-LD @type.
 * @returns {string} Zotero item type or "" when unknown.
 */
export function schemaTypeToItemType(type) {
  const list = Array.isArray(type) ? type : [type];
  for (const t of list) {
    const key = String(t || "").replace(/^.*[/#:]/, "").toLowerCase();
    if (SCHEMA_TYPES[key]) return SCHEMA_TYPES[key];
  }
  return "";
}

/** Payload keys whose values are strings (used by mergePayloads). */
export const SCALAR_KEYS = ["item_type", "title", "journal", "date", "doi", "url", "abstract", "publisher", "volume", "issue",
  "pages", "issn", "language", "pmid", "arxiv_id", "short_title", "place", "institution", "pdf_url"];

/**
 * Merge partial payloads by priority: the first source providing a non-empty value
 * wins for each field. Arrays (creators, isbn, tags) are taken from the first
 * source that has a non-empty array.
 *
 * @param {...object} sources  Partial payloads, highest priority first.
 * @returns {object} Merged payload (only non-empty keys).
 */
export function mergePayloads(...sources) {
  const out = {};
  for (const src of sources) {
    if (!src) continue;
    for (const [k, v] of Object.entries(src)) {
      if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
      if (out[k] === undefined) out[k] = v;
    }
  }
  return out;
}
