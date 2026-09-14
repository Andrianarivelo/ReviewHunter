/**
 * Translator-lite orchestrator: turns a Document into what the popup shows.
 *
 * Pure with respect to the browser: it only reads the Document it is given and
 * never touches chrome.* APIs, so the test harness can feed it fixture documents
 * created with DOMParser (whose own URL is about:blank, hence the explicit url).
 *
 * Priority of sources for a single item (first non-empty value wins per field):
 *   site translator patch > Highwire citation_* > bepress > Eprints > JSON-LD
 *   > PRISM > Dublin Core > single COinS > Open Graph / HTML fallback.
 *
 * @module extract/index
 */

import { MetaIndex, readHighwire, readEprints, readPrism, readDublinCore, readOpenGraph } from "./meta.js";
import { readJsonLd } from "./jsonld.js";
import { readCoins } from "./coins.js";
import { findPageDoi, absUrl } from "./doi.js";
import { readDoiList } from "./multi.js";
import { translatorFor } from "./sites.js";
import { clean, mergePayloads, normalizeDate } from "./normalize.js";
import { doiFromUrl, arxivIdFrom, pmidFromUrl, isLikelyPdfUrl, fileNameFromUrl } from "../shared/identifiers.js";

/** Payload keys accepted by POST /connector/save (anything else is dropped). */
export const PAYLOAD_KEYS = ["item_type", "title", "creators", "journal", "date", "doi", "url", "abstract", "publisher", "volume",
  "issue", "pages", "issn", "isbn", "language", "pmid", "arxiv_id", "short_title", "access_date", "tags", "place", "pdf_url"];

/**
 * @typedef {object} DetectedItem
 * @property {string} item_type  Zotero item type.
 * @property {string} title      Title.
 * @property {object[]} [creators]
 * @property {string} [doi]
 * @property {string} [pdf_url]  Best PDF URL known from the page.
 * @property {string[]} [_pdfCandidates]  All PDF URLs to try, best first.
 * @property {boolean} [_idOnly]  Only an identifier is known (save via /connector/identifiers).
 */

/**
 * @typedef {object} Detection
 * @property {"single"|"multiple"|"pdf"|"none"} kind  What the page contains.
 * @property {string} translator  Human label of the source ("ScienceDirect", "Embedded metadata", "Web page"...).
 * @property {string} url  Page URL.
 * @property {DetectedItem[]} items  One item for single/pdf, several for multiple.
 * @property {boolean} scholarly  Whether scholarly metadata was found.
 * @property {string} [doiVia]  Heuristic that produced the DOI when not from metadata.
 */

/**
 * Is the document itself a PDF (Chrome's viewer, a direct .pdf URL, or a
 * document whose contentType is application/pdf)?
 *
 * @param {Document} doc  Document.
 * @param {string} url    Page URL.
 * @returns {boolean} True for PDF tabs.
 */
export function isPdfDocument(doc, url) {
  if (doc && doc.contentType === "application/pdf") return true;
  // Chrome's built-in viewer: the top document is a tiny wrapper around one <embed type="application/pdf">.
  const embed = doc && doc.querySelector && doc.querySelector('embed[type="application/pdf"], embed[type="application/x-google-chrome-pdf"]');
  if (embed && doc.body && doc.body.children.length <= 2) return true;
  try {
    return new URL(url).pathname.toLowerCase().endsWith(".pdf") && !(doc && doc.querySelector && doc.querySelector("meta[name^='citation_']"));
  } catch (_) {
    return false;
  }
}

/**
 * Remove empty values, unknown keys and internal fields from a payload.
 *
 * @param {object} item  Detected item.
 * @returns {object} Payload safe to POST to /connector/save.
 */
export function toPayload(item) {
  const out = {};
  for (const key of PAYLOAD_KEYS) {
    const v = item[key];
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
    out[key] = v;
  }
  return out;
}

/**
 * Detect the reference(s) on a page.
 *
 * @param {Document} doc  Document to analyse (live page or DOMParser fixture).
 * @param {string} url    URL of the page.
 * @param {{pageData?: object}} [ctx]  Extra data read from the page world (IEEE xplGlobal).
 * @returns {Detection} Detection result.
 */
export function detect(doc, url, ctx = {}) {
  url = url || (doc && doc.URL) || "";
  if (isPdfDocument(doc, url)) return detectPdf(url);

  const translator = translatorFor(url);
  const site = translator ? safeRun(translator, doc, url, ctx) : null;
  if (site && Array.isArray(site.items)) {
    const items = site.items.map(finishItem);
    return { kind: items.length ? "multiple" : "none", translator: translator.label, url, items, scholarly: true };
  }

  const meta = new MetaIndex(doc);
  const highwire = readHighwire(meta);
  const bepress = readHighwire(meta, "bepress_citation_");
  const eprints = readEprints(meta);
  const jsonld = readJsonLd(doc);
  const prism = readPrism(meta);
  const dc = readDublinCore(meta);
  const coinsList = readCoins(doc);
  const coins = coinsList.length === 1 ? coinsList[0] : {};
  const og = readOpenGraph(meta, doc);

  const scholarly = !!(highwire.title || bepress.title || eprints.title || jsonld._scholarly || prism.doi || dc.doi || coins.title
    || (site && site.patch && (site.patch.doi || site.patch.arxiv_id || site.patch.pmid)));

  // A non-scholarly page linking to several DOIs (or holding several COinS) is a list.
  if (!scholarly && !(site && site.patch)) {
    if (coinsList.length >= 2) {
      return { kind: "multiple", translator: "COinS", url, items: coinsList.map(finishItem), scholarly: true };
    }
    const list = readDoiList(doc, url);
    if (list) {
      return { kind: "multiple", translator: "DOI links", url, items: list.map(finishItem), scholarly: true };
    }
  }

  const sitePatch = (site && site.patch) || {};
  const merged = mergePayloads(sitePatch, highwire, bepress, eprints, scholarly ? jsonld : {}, prism, dc, coins, scholarly ? {} : jsonld, og);

  // Institution of theses/reports maps to publisher in Zotero's base fields.
  if (!merged.publisher && (highwire.institution || eprints.institution)) merged.publisher = highwire.institution || eprints.institution;
  // Container for conference papers / chapters.
  if (!merged.journal && highwire.book_title) merged.journal = highwire.book_title;

  // Identifiers from the URL.
  let doiVia = merged.doi ? "metadata" : "";
  if (!merged.doi) {
    const fromUrl = doiFromUrl(url);
    if (fromUrl) {
      merged.doi = fromUrl;
      doiVia = "url";
    }
  }
  if (!merged.doi && scholarly && !(site && site.skipDoiScan)) {
    const found = findPageDoi(doc, url);
    if (found.doi) {
      merged.doi = found.doi;
      doiVia = found.via;
    }
  }
  if (!merged.arxiv_id) merged.arxiv_id = arxivIdFrom(url);
  if (!merged.pmid) merged.pmid = pmidFromUrl(url);

  // Item type: explicit sources first, then a scholarly default.
  if (!merged.item_type || (merged.item_type === "webpage" && scholarly)) {
    merged.item_type = scholarly ? (merged.arxiv_id && !merged.journal ? "preprint" : "journalArticle") : "webpage";
  }
  if (merged.item_type === "webpage" && !merged.journal) {
    try {
      merged.journal = new URL(url).hostname.replace(/^www\./, "");
    } catch (_) {
      /* ignore */
    }
  }
  if (!merged.url) merged.url = canonicalUrl(doc, url);
  merged.title = clean(merged.title) || clean(doc.title) || merged.url;
  merged.date = normalizeDate(merged.date);

  // PDF candidates: translator endpoints first, then embedded metadata, then viewer links on the page.
  const candidates = [
    ...((site && site.pdfCandidates) || []),
    highwire.pdf_url, bepress.pdf_url, eprints.pdf_url, jsonld.pdf_url,
    absUrl(attrOf(doc, 'link[rel="alternate"][type="application/pdf"]', "href"), url),
    ...(scholarly && !(site && site.skipPdfScan) ? pdfLinksOnPage(doc, url) : []),
  ].map((u) => absUrl(u, url)).filter(Boolean);
  const item = finishItem({ ...merged, _pdfCandidates: [...new Set(candidates)] });
  const label = translator ? translator.label : scholarly ? "Embedded metadata" : "Web page";
  // challenge: the page is a human-verification wall (the user must complete it before the PDF is reachable).
  return { kind: "single", translator: label, url, items: [item], scholarly, doiVia, challenge: !!(site && site.challenge) };
}

/**
 * Build the detection for a PDF tab: identifiers are taken from the URL when possible.
 *
 * @param {string} url  PDF URL.
 * @returns {Detection} Detection with kind "pdf".
 */
export function detectPdf(url) {
  const doi = doiFromUrl(url);
  const arxiv = arxivIdFrom(url);
  const name = fileNameFromUrl(url).replace(/\.pdf$/i, "").replace(/[_-]+/g, " ");
  const item = finishItem({
    item_type: doi || arxiv ? (arxiv && !doi ? "preprint" : "journalArticle") : "document",
    title: name || "PDF document",
    doi,
    arxiv_id: arxiv,
    url: arxiv ? `https://arxiv.org/abs/${arxiv}` : url,
    pdf_url: url,
    _pdfCandidates: [url],
  });
  return { kind: "pdf", translator: "PDF", url, items: [item], scholarly: !!(doi || arxiv) };
}

/**
 * Final touches common to every detected item: pdf_url from candidates, trimmed title.
 *
 * @param {object} item  Partial item.
 * @returns {DetectedItem} Item.
 */
function finishItem(item) {
  const out = { ...item };
  out.title = clean(out.title);
  if (!out.item_type) out.item_type = out.doi ? "journalArticle" : "webpage";
  const cands = out._pdfCandidates || (out.pdf_url ? [out.pdf_url] : []);
  out._pdfCandidates = cands;
  if (!out.pdf_url && cands.length) out.pdf_url = cands[0];
  delete out._scholarly;
  return out;
}

/**
 * Canonical URL of the page (link rel=canonical, og:url) or the tab URL without fragment.
 *
 * @param {Document} doc  Document.
 * @param {string} url    Tab URL.
 * @returns {string} URL.
 */
function canonicalUrl(doc, url) {
  const c = attrOf(doc, 'link[rel="canonical"]', "href") || attrOf(doc, 'meta[property="og:url"]', "content");
  const abs = absUrl(c, url);
  return abs && /^https?:/.test(abs) ? abs : String(url).replace(/#.*$/, "");
}

/**
 * Attribute of the first element matching a selector.
 *
 * @param {Document} doc   Document.
 * @param {string} sel     Selector.
 * @param {string} name    Attribute.
 * @returns {string} Value or "".
 */
function attrOf(doc, sel, name) {
  const el = doc.querySelector(sel);
  return el ? el.getAttribute(name) || "" : "";
}

/**
 * Links on an article page that look like "Download PDF" buttons.
 *
 * @param {Document} doc  Document.
 * @param {string} url    Page URL.
 * @returns {string[]} Up to 3 absolute URLs.
 */
function pdfLinksOnPage(doc, url) {
  const out = [];
  for (const a of doc.querySelectorAll("a[href]")) {
    const href = absUrl(a.getAttribute("href"), url);
    if (!href || !/^https?:/.test(href)) continue;
    const label = `${a.textContent} ${a.getAttribute("title") || ""} ${a.getAttribute("aria-label") || ""}`;
    if (isLikelyPdfUrl(href) && /pdf|download|full text/i.test(label + href)) out.push(href);
    if (out.length >= 3) break;
  }
  return out;
}

/**
 * Run a translator and swallow its exceptions (a broken site rule must never
 * prevent the generic readers from working).
 *
 * @param {object} translator  Translator.
 * @param {Document} doc       Document.
 * @param {string} url         URL.
 * @param {object} ctx         Context.
 * @returns {?object} Translator output or null.
 */
function safeRun(translator, doc, url, ctx) {
  try {
    return translator.run(doc, url, ctx) || null;
  } catch (e) {
    console.warn(`[ReviewHunter] translator ${translator.id} failed`, e);
    return null;
  }
}
