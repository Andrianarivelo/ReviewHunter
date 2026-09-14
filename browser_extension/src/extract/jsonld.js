/**
 * JSON-LD (schema.org) reader. Handles single objects, arrays, "@graph"
 * containers and nested "isPartOf" chains (issue, volume, periodical).
 *
 * @module extract/jsonld
 */

import { clean, stripTags, normalizeDate, parseCreator, schemaTypeToItemType, splitKeywords, cleanIsbns, pageRange } from "./normalize.js";
import { cleanDoi } from "../shared/identifiers.js";

/** Types we understand, most specific / most useful first. */
const TYPE_PRIORITY = ["scholarlyarticle", "medicalscholarlyarticle", "chapter", "book", "thesis", "dataset", "softwaresourcecode",
  "report", "newsarticle", "reportagenewsarticle", "blogposting", "techarticle", "videoobject", "article", "webpage", "itempage"];

/**
 * Collect every JSON-LD node of the document, flattened.
 *
 * @param {Document} doc  Document.
 * @returns {object[]} All nodes found (invalid JSON blocks are skipped).
 */
export function collectJsonLdNodes(doc) {
  const nodes = [];
  const visit = (value) => {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (Array.isArray(value["@graph"])) value["@graph"].forEach(visit);
    if (value["@type"]) nodes.push(value);
  };
  for (const el of doc.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      // Some sites wrap JSON-LD in CDATA or HTML comments.
      const text = (el.textContent || "").replace(/^\s*(<!--|\/\/<!\[CDATA\[)/, "").replace(/(-->|\/\/\]\]>)\s*$/, "");
      visit(JSON.parse(text));
    } catch (_) {
      /* invalid JSON-LD block: ignore */
    }
  }
  return nodes;
}

/**
 * Lower-cased local type names of a node.
 *
 * @param {object} node  JSON-LD node.
 * @returns {string[]} Types such as ["scholarlyarticle"].
 */
function typesOf(node) {
  const t = node["@type"];
  return (Array.isArray(t) ? t : [t]).map((x) => String(x || "").replace(/^.*[/#:]/, "").toLowerCase());
}

/**
 * Pick the node that best describes the page itself.
 *
 * @param {object[]} nodes  Flattened nodes.
 * @returns {?object} Best node or null.
 */
export function pickMainNode(nodes) {
  let best = null;
  let bestRank = Infinity;
  for (const node of nodes) {
    for (const t of typesOf(node)) {
      const rank = TYPE_PRIORITY.indexOf(t);
      if (rank !== -1 && rank < bestRank) {
        best = node;
        bestRank = rank;
      }
    }
  }
  return best;
}

/**
 * Read a text value that may be a string, an array, or an object with name/@value.
 *
 * @param {*} v  JSON-LD value.
 * @returns {string} Text.
 */
function text(v) {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return text(v[0]);
  if (typeof v === "object") return clean(v.name || v["@value"] || v.headline || v.url || "");
  return clean(v);
}

/**
 * Convert schema.org person/organisation values to creators.
 *
 * @param {*} value  author/creator/editor value.
 * @param {string} creatorType  Zotero creator type.
 * @returns {object[]} Creators.
 */
function creatorsFrom(value, creatorType) {
  const list = Array.isArray(value) ? value : value ? [value] : [];
  const out = [];
  for (const p of list) {
    if (typeof p === "string") {
      const c = parseCreator(p, creatorType);
      if (c) out.push(c);
    } else if (p && typeof p === "object") {
      const isOrg = typesOf(p).includes("organization");
      if (p.familyName || p.givenName) out.push({ creatorType, lastName: clean(text(p.familyName)), firstName: clean(text(p.givenName)) });
      else if (p.name) {
        const c = isOrg ? { creatorType, name: text(p.name) } : parseCreator(text(p.name), creatorType);
        if (c) out.push(c);
      }
    }
  }
  return out;
}

/**
 * Walk "isPartOf" to find periodical name, volume and issue numbers.
 *
 * @param {object} node  Article node.
 * @returns {{journal: string, volume: string, issue: string, issn: string}} Container info.
 */
function containerOf(node) {
  const res = { journal: "", volume: "", issue: "", issn: "" };
  let cur = node.isPartOf;
  for (let depth = 0; cur && depth < 5; depth += 1) {
    const part = Array.isArray(cur) ? cur[0] : cur;
    if (!part || typeof part !== "object") {
      if (typeof part === "string" && !res.journal) res.journal = clean(part);
      break;
    }
    const types = typesOf(part);
    if (types.includes("publicationissue") && !res.issue) res.issue = text(part.issueNumber);
    if (types.includes("publicationvolume") && !res.volume) res.volume = text(part.volumeNumber);
    if ((types.includes("periodical") || types.includes("book") || types.includes("website") || types.includes("creativeworkseries")) && !res.journal) {
      res.journal = text(part.name);
      res.issn = text(part.issn);
    }
    cur = part.isPartOf;
  }
  return res;
}

/**
 * Extract a DOI from a node's identifier / sameAs / url / @id fields.
 *
 * @param {object} node  JSON-LD node.
 * @returns {string} DOI or "".
 */
function doiOf(node) {
  const values = [node.doi, node.identifier, node.sameAs, node["@id"], node.url].flat().filter(Boolean);
  for (const v of values) {
    const s = typeof v === "object" ? (v.value || v["@value"] || v.propertyID === "DOI" && v.value || "") : v;
    const doi = /10\.\d{4,9}\//.test(String(s)) ? cleanDoi(String(s), { fromUrl: /^https?:/.test(String(s)) }) : "";
    if (doi) return doi;
  }
  return "";
}

/**
 * Convert the best JSON-LD node of a document into a partial payload.
 *
 * @param {Document} doc  Document.
 * @returns {object} Partial payload; includes `_scholarly: true` for article-like scholarly types.
 */
export function readJsonLd(doc) {
  const nodes = collectJsonLdNodes(doc);
  const node = pickMainNode(nodes);
  if (!node) return {};
  const types = typesOf(node);
  const item_type = schemaTypeToItemType(node["@type"]) || "webpage";
  const container = containerOf(node);
  const publisher = text(node.publisher);
  const creators = [...creatorsFrom(node.author || node.creator, item_type === "computerProgram" ? "programmer" : "author"),
    ...creatorsFrom(node.editor, "editor")];
  let pdf = "";
  for (const enc of [node.encoding, node.associatedMedia, node.distribution].flat().filter(Boolean)) {
    if (enc && typeof enc === "object" && /pdf/i.test(enc.encodingFormat || enc.fileFormat || "") && (enc.contentUrl || enc.url)) {
      pdf = enc.contentUrl || enc.url;
      break;
    }
  }
  return {
    _scholarly: types.some((t) => ["scholarlyarticle", "medicalscholarlyarticle", "chapter", "thesis", "dataset"].includes(t)) || !!doiOf(node),
    item_type,
    title: text(node.headline) || text(node.name),
    creators,
    date: normalizeDate(text(node.datePublished) || text(node.dateCreated) || text(node.uploadDate) || text(node.dateModified)),
    journal: container.journal || (item_type === "blogPost" || item_type === "webpage" || item_type === "newspaperArticle" ? publisher : ""),
    volume: text(node.volumeNumber) || container.volume,
    issue: text(node.issueNumber) || container.issue,
    pages: text(node.pagination) || pageRange(text(node.pageStart), text(node.pageEnd)),
    doi: doiOf(node),
    url: typeof node.url === "string" ? node.url : "",
    abstract: stripTags(text(node.abstract) || text(node.description)),
    publisher,
    issn: container.issn,
    isbn: cleanIsbns([node.isbn].flat().filter(Boolean)),
    language: text(node.inLanguage),
    tags: splitKeywords([node.keywords].flat().filter(Boolean).map((k) => (typeof k === "object" ? text(k) : k))),
    pdf_url: pdf,
  };
}
