/**
 * Embedded metadata readers: Highwire Press (citation_*), bepress, Eprints,
 * PRISM, Dublin Core, Open Graph and Twitter cards.
 *
 * Every reader takes a {@link MetaIndex} (built once per document) and returns a
 * partial ReviewHunter payload. The orchestrator in extract/index.js merges them
 * by priority.
 *
 * @module extract/meta
 */

import { clean, stripTags, normalizeDate, pageRange, parseCreators, splitKeywords, cleanIsbns } from "./normalize.js";
import { cleanDoi, arxivIdFrom } from "../shared/identifiers.js";

/**
 * Index of all <meta> tags keyed by lower-cased name/property.
 * Values keep document order, so multi-valued tags (citation_author) are preserved.
 */
export class MetaIndex {
  /**
   * @param {Document} doc  Document to index.
   */
  constructor(doc) {
    /** @type {Map<string, string[]>} */
    this.map = new Map();
    for (const el of doc.querySelectorAll("meta")) {
      const key = (el.getAttribute("name") || el.getAttribute("property") || el.getAttribute("itemprop") || "").trim().toLowerCase();
      const value = el.getAttribute("content");
      if (!key || value === null) continue;
      const v = clean(value);
      if (!v) continue;
      if (!this.map.has(key)) this.map.set(key, []);
      this.map.get(key).push(v);
    }
  }

  /**
   * First value of the first key that exists.
   *
   * @param {...string} keys  Candidate names (case-insensitive).
   * @returns {string} Value or "".
   */
  get(...keys) {
    for (const k of keys) {
      const v = this.map.get(k.toLowerCase());
      if (v && v.length) return v[0];
    }
    return "";
  }

  /**
   * All values of the first key that exists.
   *
   * @param {...string} keys  Candidate names.
   * @returns {string[]} Values (possibly empty).
   */
  all(...keys) {
    for (const k of keys) {
      const v = this.map.get(k.toLowerCase());
      if (v && v.length) return v;
    }
    return [];
  }

  /**
   * True when at least one key starts with the prefix.
   *
   * @param {string} prefix  Lower-case prefix, e.g. "citation_".
   * @returns {boolean} Whether such tags exist.
   */
  hasPrefix(prefix) {
    for (const k of this.map.keys()) if (k.startsWith(prefix)) return true;
    return false;
  }
}

/**
 * Highwire Press tags (Google Scholar inclusion guidelines), also used with the
 * "bepress_" prefix by Digital Commons repositories.
 *
 * @param {MetaIndex} meta  Index of the document meta tags.
 * @param {string} [prefix="citation_"]  "citation_" or "bepress_citation_".
 * @returns {object} Partial payload (empty object when no such tags).
 */
export function readHighwire(meta, prefix = "citation_") {
  if (!meta.hasPrefix(prefix)) return {};
  const g = (...names) => meta.get(...names.map((n) => prefix + n));
  const all = (...names) => meta.all(...names.map((n) => prefix + n));
  const out = {};
  out.title = g("title");
  let authors = all("author");
  if (!authors.length && g("authors")) authors = [g("authors")];
  out.creators = parseCreators(authors);
  const editors = all("editor");
  if (editors.length) out.creators = [...out.creators, ...parseCreators(editors, "editor")];
  out.date = normalizeDate(g("publication_date", "date", "cover_date", "online_date", "year"));
  out.conference = g("conference_title", "conference");
  out.journal = g("journal_title", "journal_abbrev") || out.conference || g("inbook_title", "book_title", "series_title");
  out.book_title = g("inbook_title", "book_title");
  out.volume = g("volume");
  out.issue = g("issue");
  out.pages = pageRange(g("firstpage"), g("lastpage"));
  out.doi = cleanDoi(g("doi"));
  out.pdf_url = g("pdf_url");
  out.publisher = g("publisher");
  out.isbn = cleanIsbns(all("isbn"));
  out.issn = g("issn", "eissn");
  out.language = g("language");
  out.institution = g("dissertation_institution", "technical_report_institution");
  out.abstract = stripTags(g("abstract"));
  out.abstract_url = g("abstract_html_url");
  out.fulltext_url = g("fulltext_html_url", "public_url");
  out.pmid = g("pmid");
  out.arxiv_id = arxivIdFrom(`arXiv:${g("arxiv_id")}`);
  out.report_number = g("technical_report_number");
  out.tags = splitKeywords(all("keywords", "keyword"));
  // Item type hints from which tags exist.
  if (g("dissertation_institution", "dissertation_name")) out.item_type = "thesis";
  else if (g("technical_report_institution", "technical_report_number")) out.item_type = "report";
  else if (out.conference) out.item_type = "conferencePaper";
  else if (g("inbook_title")) out.item_type = "bookSection";
  else if (out.isbn.length && !g("journal_title")) out.item_type = "book";
  else if (out.arxiv_id && !g("journal_title")) out.item_type = "preprint";
  else if (out.title) out.item_type = "journalArticle";
  return out;
}

/**
 * Eprints repository tags (eprints.title, eprints.creators_name ...).
 *
 * @param {MetaIndex} meta  Meta index.
 * @returns {object} Partial payload.
 */
export function readEprints(meta) {
  if (!meta.hasPrefix("eprints.")) return {};
  const type = meta.get("eprints.type").toLowerCase();
  const typeMap = { article: "journalArticle", book: "book", book_section: "bookSection", conference_item: "conferencePaper",
    thesis: "thesis", monograph: "report", patent: "patent", dataset: "dataset", preprint: "preprint" };
  const docUrl = meta.all("eprints.document_url").find((u) => /\.pdf(\?|$)/i.test(u)) || "";
  return {
    item_type: typeMap[type] || (meta.get("eprints.title") ? "journalArticle" : ""),
    title: meta.get("eprints.title"),
    creators: parseCreators(meta.all("eprints.creators_name")),
    date: normalizeDate(meta.get("eprints.date")),
    journal: meta.get("eprints.publication", "eprints.event_title", "eprints.book_title"),
    volume: meta.get("eprints.volume"),
    issue: meta.get("eprints.number"),
    pages: meta.get("eprints.pagerange"),
    doi: cleanDoi(meta.get("eprints.id_number", "eprints.doi")),
    publisher: meta.get("eprints.publisher"),
    issn: meta.get("eprints.issn"),
    isbn: cleanIsbns(meta.all("eprints.isbn")),
    institution: meta.get("eprints.institution"),
    abstract: stripTags(meta.get("eprints.abstract")),
    tags: splitKeywords(meta.all("eprints.keywords")),
    pdf_url: docUrl,
  };
}

/**
 * PRISM tags (prism.doi, prism.publicationName, prism.volume ...).
 *
 * @param {MetaIndex} meta  Meta index.
 * @returns {object} Partial payload.
 */
export function readPrism(meta) {
  if (!meta.hasPrefix("prism.")) return {};
  return {
    doi: cleanDoi(meta.get("prism.doi")),
    journal: meta.get("prism.publicationname"),
    volume: meta.get("prism.volume"),
    issue: meta.get("prism.number", "prism.issueidentifier"),
    pages: pageRange(meta.get("prism.startingpage"), meta.get("prism.endingpage")),
    date: normalizeDate(meta.get("prism.publicationdate", "prism.coverdate")),
    issn: meta.get("prism.issn", "prism.eissn"),
    isbn: cleanIsbns(meta.all("prism.isbn")),
    url: meta.get("prism.url"),
    title: meta.get("prism.title"),
  };
}

/**
 * Dublin Core (DC.*, dc.*, DCTERMS.*), commonly exposed by repositories, OJS
 * journals and some publishers.
 *
 * @param {MetaIndex} meta  Meta index.
 * @returns {object} Partial payload.
 */
export function readDublinCore(meta) {
  if (!meta.hasPrefix("dc.") && !meta.hasPrefix("dcterms.")) return {};
  const ids = [...meta.all("dc.identifier"), ...meta.all("dcterms.identifier")];
  let doi = "";
  let isbn = [];
  for (const id of ids) {
    if (!doi && /10\.\d{4,9}\//.test(id)) doi = cleanDoi(id);
    if (/isbn/i.test(id)) isbn = cleanIsbns(id);
  }
  const typ = meta.get("dc.type", "dcterms.type").toLowerCase();
  let item_type = "";
  if (/thesis|dissertation/.test(typ)) item_type = "thesis";
  else if (/book part|chapter/.test(typ)) item_type = "bookSection";
  else if (/^book/.test(typ)) item_type = "book";
  else if (/conference/.test(typ)) item_type = "conferencePaper";
  else if (/report/.test(typ)) item_type = "report";
  else if (/dataset/.test(typ)) item_type = "dataset";
  else if (/article|text/.test(typ) && doi) item_type = "journalArticle";
  return {
    item_type,
    title: meta.get("dc.title", "dcterms.title"),
    creators: parseCreators(meta.all("dc.creator", "dcterms.creator", "dc.creator.personalname", "dc.contributor.author")),
    date: normalizeDate(meta.get("dcterms.issued", "dc.date.issued", "dc.date", "dcterms.date", "dcterms.created")),
    publisher: meta.get("dc.publisher", "dcterms.publisher"),
    journal: meta.get("dc.source", "dcterms.ispartof", "dcterms.bibliographiccitation.jtitle"),
    language: meta.get("dc.language", "dcterms.language"),
    abstract: stripTags(meta.get("dcterms.abstract", "dc.description", "dcterms.description")),
    tags: splitKeywords(meta.all("dc.subject", "dcterms.subject")),
    doi,
    isbn,
  };
}

/**
 * Open Graph / Twitter card / plain HTML metadata, the generic web-page fallback.
 *
 * @param {MetaIndex} meta  Meta index.
 * @param {Document} doc   Document (for <title>, <html lang>, canonical link).
 * @returns {object} Partial payload with item_type "webpage" or "blogPost"/"newspaperArticle" hints.
 */
export function readOpenGraph(meta, doc) {
  const ogType = meta.get("og:type").toLowerCase();
  const title = meta.get("og:title", "twitter:title") || clean(doc.title);
  const authors = meta.all("author", "article:author", "parsely-author", "sailthru.author", "byl");
  const creators = parseCreators(authors.filter((a) => !/^https?:\/\//.test(a)).map((a) => a.replace(/^by\s+/i, "")));
  return {
    item_type: ogType === "article" && meta.get("article:section") && /news/i.test(meta.get("og:site_name")) ? "newspaperArticle" : "",
    title,
    journal: meta.get("og:site_name", "application-name", "twitter:site").replace(/^@/, ""),
    creators,
    date: normalizeDate(meta.get("article:published_time", "date", "pubdate", "publish-date", "parsely-pub-date", "sailthru.date", "dcterms.modified", "article:modified_time")),
    abstract: stripTags(meta.get("og:description", "description", "twitter:description")),
    language: meta.get("og:locale").replace("_", "-") || clean(doc.documentElement && doc.documentElement.getAttribute("lang")),
    tags: splitKeywords(meta.all("article:tag", "news_keywords")),
  };
}
