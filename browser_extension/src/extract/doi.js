/**
 * DOI discovery in a rendered document: DOI-labelled elements, doi.org links
 * (ranked by frequency) and, as a last resort, the page text.
 *
 * @module extract/doi
 */

import { clean } from "./normalize.js";
import { doiFromUrl, extractDoi, cleanDoi } from "../shared/identifiers.js";

/**
 * Resolve an href against the page URL without throwing.
 *
 * @param {string} href     Raw href attribute.
 * @param {string} baseUrl  Page URL.
 * @returns {string} Absolute URL or "".
 */
export function absUrl(href, baseUrl) {
  if (!href) return "";
  try {
    return new URL(href, baseUrl).href;
  } catch (_) {
    return "";
  }
}

/**
 * Collect DOI links (doi.org resolvers and "/doi/10.x" publisher links).
 *
 * @param {Document} doc      Document.
 * @param {string} baseUrl    Page URL.
 * @returns {Map<string, {doi: string, count: number, anchors: Element[]}>} Keyed by lower-cased DOI, document order.
 */
export function collectDoiLinks(doc, baseUrl) {
  const map = new Map();
  for (const a of doc.querySelectorAll("a[href]")) {
    const href = absUrl(a.getAttribute("href"), baseUrl);
    if (!href || !/(^https?:\/\/(dx\.)?doi\.org\/)|\/doi\/(abs\/|full\/)?10\.\d{4,9}\//i.test(href)) continue;
    const doi = doiFromUrl(href);
    if (!doi) continue;
    const key = doi.toLowerCase();
    if (!map.has(key)) map.set(key, { doi, count: 0, anchors: [] });
    const entry = map.get(key);
    entry.count += 1;
    entry.anchors.push(a);
  }
  return map;
}

/**
 * Find the DOI of the page itself when metadata tags do not provide one.
 *
 * Order: elements whose class/id mentions "doi" (article header widgets),
 * the most frequent DOI link, then the first DOI in the visible text.
 *
 * @param {Document} doc    Document.
 * @param {string} baseUrl  Page URL.
 * @returns {{doi: string, via: string}} DOI (maybe "") and which heuristic found it.
 */
export function findPageDoi(doc, baseUrl) {
  for (const el of doc.querySelectorAll('[class*="doi" i], [id*="doi" i], [data-doi]')) {
    const candidate = el.getAttribute("data-doi") || (el.getAttribute("href") || "") + " " + (el.textContent || "").slice(0, 300);
    const doi = /10\.\d{4,9}\//.test(candidate) ? cleanDoi(extractDoi(candidate) || doiFromUrl(el.getAttribute("href") || "")) : "";
    if (doi) return { doi, via: "doi element" };
  }
  const links = [...collectDoiLinks(doc, baseUrl).values()].sort((a, b) => b.count - a.count);
  if (links.length === 1 || (links.length > 1 && links[0].count > links[1].count)) return { doi: links[0].doi, via: "doi link" };
  const body = doc.body ? clean(doc.body.textContent).slice(0, 20000) : "";
  const doi = extractDoi(body);
  return { doi, via: doi ? "page text" : "" };
}
