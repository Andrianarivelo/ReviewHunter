/**
 * Multi-item pages: Google Scholar results, PubMed search results, CrossRef
 * search and generic pages listing several DOI links (reading lists, reference
 * sections of blog posts, lab publication pages).
 *
 * Each reader returns an array of partial payloads, or null when the page is not
 * of that kind. Entries carrying only an identifier have `_idOnly: true` and are
 * saved server-side through /connector/identifiers.
 *
 * @module extract/multi
 */

import { clean, parseCreators, yearOf } from "./normalize.js";
import { doiFromUrl, arxivIdFrom, isLikelyPdfUrl, cleanDoi } from "../shared/identifiers.js";
import { absUrl, collectDoiLinks } from "./doi.js";

/**
 * Google Scholar result list (scholar.google.* /scholar?q=...).
 *
 * @param {Document} doc  Document.
 * @param {string} url    Page URL.
 * @returns {?object[]} Items or null.
 */
export function readGoogleScholar(doc, url) {
  if (!/^https?:\/\/scholar\.google\.[^/]+\/(scholar|citations)/i.test(url)) return null;
  const items = [];
  const seen = new Set();
  for (const row of doc.querySelectorAll(".gs_r.gs_or, .gs_r .gs_ri, .gs_ri")) {
    const ri = row.classList.contains("gs_ri") ? row : row.querySelector(".gs_ri");
    const box = row.classList.contains("gs_ri") ? row.closest(".gs_r") || row : row;
    // Both the row and its inner .gs_ri match the selector: process each result once (never mutate the page).
    if (!ri || seen.has(ri)) continue;
    seen.add(ri);
    const a = ri.querySelector("h3.gs_rt a, .gs_rt a");
    const h3 = ri.querySelector("h3.gs_rt, .gs_rt");
    if (!h3) continue;
    // Remove Scholar's "[PDF]" / "[CITATION]" badges from the title.
    const title = clean((a || h3).textContent.replace(/^\s*(\[[A-Z]+\]\s*)+/, ""));
    const href = a ? absUrl(a.getAttribute("href"), url) : "";
    const byline = clean((ri.querySelector(".gs_a") || {}).textContent || "");
    // "A Author, B Author - Journal, 2017 - publisher.com"
    const [authorsPart = "", venuePart = ""] = byline.split(/\s+-\s+/);
    const authors = authorsPart.split(",").map((s) => s.replace(/…|\.\.\./g, "").trim()).filter((s) => s && !/^\d{4}$/.test(s));
    const pdfA = box.querySelector(".gs_or_ggsm a, .gs_ggs a");
    const pdf = pdfA ? absUrl(pdfA.getAttribute("href"), url) : "";
    const doi = doiFromUrl(href);
    const arxiv = arxivIdFrom(href) || arxivIdFrom(pdf);
    items.push({
      item_type: arxiv && !doi ? "preprint" : "journalArticle",
      title,
      creators: parseCreators(authors),
      journal: clean(venuePart.replace(/,?\s*\d{4}\s*$/, "")),
      date: yearOf(venuePart),
      doi,
      arxiv_id: arxiv,
      url: href,
      pdf_url: pdf && (isLikelyPdfUrl(pdf) || /\[PDF\]/i.test(pdfA.textContent)) ? pdf : "",
    });
  }
  return items.filter((i) => i.title);
}

/**
 * PubMed search results (pubmed.ncbi.nlm.nih.gov/?term=...).
 *
 * @param {Document} doc  Document.
 * @param {string} url    Page URL.
 * @returns {?object[]} Items or null.
 */
export function readPubMedSearch(doc, url) {
  if (!/^https?:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/(\?|$)/i.test(url)) return null;
  const items = [];
  for (const art of doc.querySelectorAll("article.full-docsum, .docsum-wrap")) {
    const a = art.querySelector("a.docsum-title");
    if (!a) continue;
    const pmid = clean((art.querySelector(".docsum-pmid") || {}).textContent || "") || (a.getAttribute("href") || "").replace(/\D/g, "");
    const cite = clean((art.querySelector(".full-journal-citation, .short-journal-citation") || {}).textContent || "");
    items.push({
      item_type: "journalArticle",
      title: clean(a.textContent),
      creators: parseCreators(clean((art.querySelector(".full-authors, .short-authors") || {}).textContent || "").replace(/\.$/, "").split(",")),
      journal: clean(cite.split(".")[0]),
      date: yearOf(cite),
      doi: cleanDoi((cite.match(/doi:\s*(10\.\d{4,9}\/\S+)/i) || [])[1] || ""),
      pmid,
      url: `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`,
    });
  }
  return items;
}

/**
 * CrossRef metadata search (search.crossref.org).
 *
 * @param {Document} doc  Document.
 * @param {string} url    Page URL.
 * @returns {?object[]} Items or null.
 */
export function readCrossRefSearch(doc, url) {
  if (!/^https?:\/\/search\.crossref\.org\//i.test(url)) return null;
  const items = [];
  const seen = new Set();
  for (const row of doc.querySelectorAll(".result, tr.result, .search-result, table tr")) {
    const link = [...row.querySelectorAll("a[href]")].find((a) => /doi\.org\/10\./i.test(a.getAttribute("href")));
    if (!link) continue;
    const doi = doiFromUrl(link.getAttribute("href"));
    if (!doi || seen.has(doi.toLowerCase())) continue;
    seen.add(doi.toLowerCase());
    const titleEl = row.querySelector("p.lead, .lead, .title, h3, h4");
    const extra = clean((row.querySelector(".extra, .expand") || {}).textContent || "");
    items.push({ _idOnly: true, title: clean(titleEl ? titleEl.textContent : "") || doi, doi, date: yearOf(extra), url: `https://doi.org/${doi}` });
  }
  return items;
}

/**
 * Generic page listing at least two distinct DOIs in its links.
 * The title shown for each entry is the link text when it is not the bare DOI,
 * otherwise the text of the surrounding list item / paragraph / table row.
 *
 * @param {Document} doc  Document.
 * @param {string} url    Page URL.
 * @returns {?object[]} Id-only items or null when fewer than 2 distinct DOIs.
 */
export function readDoiList(doc, url) {
  const links = collectDoiLinks(doc, url);
  if (links.size < 2) return null;
  const items = [];
  for (const { doi, anchors } of links.values()) {
    const a = anchors[0];
    let label = clean(a.textContent);
    if (!label || /10\.\d{4,9}\//.test(label) || /^(doi|link|here|full text|pdf|article)$/i.test(label)) {
      const block = a.closest("li, p, tr, dd, .citation, .reference, article, div");
      label = clean(block ? block.textContent : "").replace(/(https?:\/\/(dx\.)?doi\.org\/)?10\.\d{4,9}\/\S+/gi, "").replace(/\bdoi:?\s*$/i, "").trim();
    }
    items.push({ _idOnly: true, title: label.slice(0, 220) || doi, doi, url: `https://doi.org/${doi}` });
  }
  return items;
}
