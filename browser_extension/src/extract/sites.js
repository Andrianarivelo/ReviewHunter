/**
 * Site-specific "translators". Generic embedded metadata already covers most
 * publishers; these add what the generic readers cannot know: identifiers hidden
 * in URLs, reliable PDF endpoints, item types and pages without meta tags.
 *
 * A translator is `{ id, label, test(url), run(doc, url, ctx) }` where `run`
 * returns `{ patch?, pdfCandidates?, skipDoiScan?, items? }`:
 * - patch: partial payload that overrides generic metadata;
 * - pdfCandidates: ordered PDF URLs to try in the browser;
 * - skipDoiScan: do not guess a DOI from links/text (e.g. arXiv shows DataCite DOIs);
 * - items: a list for multi-item pages (then patch is ignored).
 *
 * @module extract/sites
 */

import { clean, stripTags, normalizeDate, pageRange, parseCreators, cleanIsbns, splitKeywords } from "./normalize.js";
import { arxivIdFrom, pmidFromUrl, piiFromUrl, doiFromUrl, cleanDoi, extractDoi } from "../shared/identifiers.js";
import { absUrl } from "./doi.js";
import { readGoogleScholar, readPubMedSearch, readCrossRefSearch } from "./multi.js";

/**
 * querySelector text helper.
 *
 * @param {ParentNode} root  Element or document.
 * @param {string} sel       CSS selector.
 * @returns {string} Clean text or "".
 */
function txt(root, sel) {
  const el = root.querySelector(sel);
  return el ? clean(el.textContent) : "";
}

/**
 * querySelector attribute helper.
 *
 * @param {ParentNode} root  Element or document.
 * @param {string} sel       CSS selector.
 * @param {string} attr      Attribute name.
 * @returns {string} Attribute value or "".
 */
function attr(root, sel, attr) {
  const el = root.querySelector(sel);
  return el ? clean(el.getAttribute(attr)) : "";
}

/**
 * Find a JSON object assigned in an inline script (e.g. `xplGlobal.document.metadata={...};`).
 *
 * @param {Document} doc     Document.
 * @param {RegExp} marker    Regex matching the text right before the JSON object.
 * @returns {?object} Parsed object or null.
 */
function jsonFromScripts(doc, marker) {
  for (const s of doc.querySelectorAll("script:not([src])")) {
    const code = s.textContent || "";
    const m = marker.exec(code);
    if (!m) continue;
    const start = m.index + m[0].length;
    // Walk braces to find the end of the object literal (strings are skipped).
    let depth = 0;
    let inStr = false;
    let esc = false;
    for (let i = start; i < code.length; i += 1) {
      const c = code[i];
      if (inStr) {
        if (esc) esc = false;
        else if (c === "\\") esc = true;
        else if (c === '"') inStr = false;
      } else if (c === '"') inStr = true;
      else if (c === "{") depth += 1;
      else if (c === "}") {
        depth -= 1;
        if (depth === 0) {
          try {
            return JSON.parse(code.slice(start, i + 1));
          } catch (_) {
            return null;
          }
        }
      }
    }
  }
  return null;
}

/** @type {Array<{id: string, label: string, test: (url: string) => boolean, run: Function}>} */
export const TRANSLATORS = [
  {
    id: "scholar",
    label: "Google Scholar",
    test: (url) => /^https?:\/\/scholar\.google\.[^/]+\/(scholar|citations)/i.test(url),
    run: (doc, url) => ({ items: readGoogleScholar(doc, url) || [] }),
  },
  {
    id: "pubmed-search",
    label: "PubMed search",
    test: (url) => /^https?:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/(\?|$)/i.test(url),
    run: (doc, url) => ({ items: readPubMedSearch(doc, url) || [] }),
  },
  {
    id: "crossref-search",
    label: "CrossRef search",
    test: (url) => /^https?:\/\/search\.crossref\.org\//i.test(url),
    run: (doc, url) => ({ items: readCrossRefSearch(doc, url) || [] }),
  },
  {
    id: "arxiv",
    label: "arXiv",
    test: (url) => /^https?:\/\/(www\.|export\.)?arxiv\.org\/(abs|html)\//i.test(url),
    run: (doc, url) => {
      const id = arxivIdFrom(url);
      // Journal DOI shown by arXiv ("td.doi" / citation_doi). The 10.48550 DataCite DOI is not used.
      let doi = cleanDoi(attr(doc, 'meta[name="citation_doi"]', "content")) || doiFromUrl(attr(doc, "td.doi a, .tablecell.doi a", "href"));
      if (/^10\.48550\//.test(doi)) doi = "";
      const title = attr(doc, 'meta[name="citation_title"]', "content") || txt(doc, "h1.title").replace(/^Title:\s*/i, "");
      const authors = [...doc.querySelectorAll('meta[name="citation_author"]')].map((m) => m.getAttribute("content"));
      return {
        skipDoiScan: true,
        patch: {
          item_type: doi ? "journalArticle" : "preprint",
          title,
          creators: authors.length ? parseCreators(authors) : parseCreators([...doc.querySelectorAll(".authors a")].map((a) => a.textContent)),
          arxiv_id: id,
          doi,
          publisher: "arXiv",
          journal: doi ? "" : "arXiv",
          abstract: stripTags(attr(doc, 'meta[name="citation_abstract"]', "content") || txt(doc, "blockquote.abstract").replace(/^Abstract:\s*/i, "")),
          url: id ? `https://arxiv.org/abs/${id}` : url,
          date: normalizeDate(attr(doc, 'meta[name="citation_date"]', "content") || attr(doc, 'meta[name="citation_online_date"]', "content")),
        },
        pdfCandidates: id ? [`https://arxiv.org/pdf/${id}`] : [],
      };
    },
  },
  {
    id: "pubmed",
    label: "PubMed",
    test: (url) => !!pmidFromUrl(url),
    run: (doc, url) => {
      const pmid = pmidFromUrl(url);
      const doi = cleanDoi(attr(doc, 'meta[name="citation_doi"]', "content")) || doiFromUrl(attr(doc, ".identifiers .doi a, a[data-ga-action='DOI']", "href"))
        || extractDoi(txt(doc, ".identifiers .doi, .identifier.doi"));
      const pmcid = (txt(doc, ".identifiers .pmc, .identifier.pmc").match(/PMC\d+/i) || [])[0] || "";
      const cands = [];
      if (pmcid) cands.push(`https://pmc.ncbi.nlm.nih.gov/articles/${pmcid.toUpperCase()}/pdf/`);
      return {
        skipDoiScan: !doi,
        patch: {
          item_type: "journalArticle",
          pmid,
          doi,
          title: attr(doc, 'meta[name="citation_title"]', "content") || txt(doc, "h1.heading-title"),
          abstract: txt(doc, "#eng-abstract, .abstract-content"),
          url: `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`,
          tags: splitKeywords(txt(doc, "#mesh-terms .keyword-actions-trigger") ? [...doc.querySelectorAll("#mesh-terms .keyword-actions-trigger")].map((b) => b.textContent) : []),
        },
        pdfCandidates: cands,
      };
    },
  },
  {
    id: "pmc",
    label: "PubMed Central",
    test: (url) => /(pmc\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov\/pmc)\/articles\/PMC\d+/i.test(url),
    run: (doc, url) => {
      const base = url.replace(/[?#].*$/, "").replace(/\/?$/, "/").replace(/\/pdf\/.*$/, "/");
      const pdfLink = [...doc.querySelectorAll('a[href*=".pdf"], a[data-ga-label*="pdf" i]')].map((a) => absUrl(a.getAttribute("href"), url)).find(Boolean);
      return {
        patch: { item_type: "journalArticle", pmid: attr(doc, 'meta[name="citation_pmid"]', "content") },
        pdfCandidates: [pdfLink, `${base}pdf/`].filter(Boolean),
      };
    },
  },
  {
    id: "biorxiv",
    label: "bioRxiv / medRxiv",
    test: (url) => /^https?:\/\/(www\.)?(biorxiv|medrxiv)\.org\/content\//i.test(url),
    run: (doc, url) => {
      const server = /medrxiv/i.test(url) ? "medRxiv" : "bioRxiv";
      const base = url.replace(/[?#].*$/, "").replace(/\.(full|abstract|article-info|article-metrics|supplementary-material)(\.pdf)?(\+html)?$/, "");
      return {
        patch: { item_type: "preprint", journal: server, publisher: attr(doc, 'meta[name="citation_publisher"]', "content") || "Cold Spring Harbor Laboratory" },
        pdfCandidates: [attr(doc, 'meta[name="citation_pdf_url"]', "content"), `${base}.full.pdf`].filter(Boolean),
      };
    },
  },
  {
    id: "sciencedirect",
    label: "ScienceDirect",
    test: (url) => /^https?:\/\/(www\.)?sciencedirect\.com\/science\/article\//i.test(url),
    run: (doc, url) => {
      const pii = piiFromUrl(url) || attr(doc, 'meta[name="citation_pii"]', "content");
      const cands = [];
      // The article page embeds its state as JSON; pdfDownload.linkToPdf carries the signed md5/pid link.
      for (const s of doc.querySelectorAll('script[type="application/json"], script:not([src])')) {
        const m = (s.textContent || "").match(/"linkToPdf"\s*:\s*"([^"]+)"/);
        if (m) {
          cands.push(absUrl(m[1].replace(/\\u002F/gi, "/").replace(/\\\//g, "/").replace(/\\u0026/gi, "&"), url));
          break;
        }
      }
      const pdfLink = attr(doc, 'a[href*="/pdfft"], a.pdf-download, a[aria-label*="PDF" i][href]', "href");
      if (pdfLink) cands.push(absUrl(pdfLink, url));
      const metaPdf = attr(doc, 'meta[name="citation_pdf_url"]', "content");
      if (metaPdf) cands.push(metaPdf);
      if (pii) cands.push(`https://www.sciencedirect.com/science/article/pii/${pii}/pdfft?isDTMRedir=true&download=true`);
      const doi = cleanDoi(attr(doc, 'meta[name="citation_doi"]', "content")) || doiFromUrl(attr(doc, "a.doi, a.anchor.doi", "href"));
      return {
        patch: {
          doi,
          url: pii ? `https://www.sciencedirect.com/science/article/pii/${pii}` : url,
          publisher: attr(doc, 'meta[name="citation_publisher"]', "content") || "Elsevier",
          journal: attr(doc, 'meta[name="citation_journal_title"]', "content") || txt(doc, ".publication-title-link, h2.publication-title"),
          abstract: txt(doc, "#abstracts .abstract.author, .abstract.author"),
          title: attr(doc, 'meta[name="citation_title"]', "content") || txt(doc, "h1 .title-text, span.title-text"),
          item_type: "journalArticle",
        },
        pdfCandidates: cands,
      };
    },
  },
  {
    id: "springer-nature",
    label: "Springer Nature",
    test: (url) => /^https?:\/\/(link\.springer\.com|www\.nature\.com|(www\.)?biomedcentral\.com|[a-z]+\.biomedcentral\.com)\//i.test(url),
    run: (doc, url) => {
      const doi = cleanDoi(attr(doc, 'meta[name="citation_doi"], meta[name="DOI"], meta[name="dc.identifier"]', "content"));
      const cands = [attr(doc, 'meta[name="citation_pdf_url"]', "content")];
      if (/nature\.com\/articles\//i.test(url)) cands.push(`${url.replace(/[?#].*$/, "").replace(/\.pdf$/, "")}.pdf`);
      if (doi && /springer/i.test(url)) cands.push(`https://link.springer.com/content/pdf/${doi}.pdf`);
      const isChapter = /\/chapter\//i.test(url);
      return {
        patch: {
          item_type: isChapter ? "bookSection" : /\/book\//i.test(url) ? "book" : "",
          doi,
        },
        pdfCandidates: cands.filter(Boolean),
      };
    },
  },
  {
    id: "wiley",
    label: "Wiley",
    test: (url) => /^https?:\/\/([a-z0-9-]+\.)?onlinelibrary\.wiley\.com\/doi\//i.test(url),
    run: (doc, url) => {
      const doi = doiFromUrl(url) || cleanDoi(attr(doc, 'meta[name="citation_doi"]', "content"));
      const origin = new URL(url).origin;
      return {
        patch: { doi },
        pdfCandidates: doi ? [`${origin}/doi/pdfdirect/${doi}?download=true`, `${origin}/doi/pdf/${doi}`] : [],
      };
    },
  },
  {
    id: "ieee",
    label: "IEEE Xplore",
    test: (url) => /^https?:\/\/ieeexplore\.ieee\.org\/(abstract\/)?document\/\d+/i.test(url),
    run: (doc, url, ctx) => {
      // Page-world data (xplGlobal.document.metadata) is passed in ctx.pageData by the service worker;
      // saved pages / tests fall back to parsing the inline script.
      const md = (ctx && ctx.pageData) || jsonFromScripts(doc, /xplGlobal\.document\.metadata\s*=\s*/) || {};
      const arnumber = String(md.articleNumber || (url.match(/document\/(\d+)/) || [])[1] || "");
      const creators = (md.authors || []).map((a) => (a.lastName || a.firstName
        ? { creatorType: "author", lastName: clean(a.lastName), firstName: clean(a.firstName) }
        : parseCreators([a.name || ""])[0])).filter(Boolean);
      const isConf = md.isConference || /conference/i.test(md.contentType || "");
      const isBook = md.isBook || /book/i.test(md.contentType || "");
      const issn = (md.issn || []).map((i) => i.value).find(Boolean) || "";
      const isbn = cleanIsbns((md.isbn || []).map((i) => i.value));
      return {
        patch: {
          item_type: isConf ? "conferencePaper" : isBook ? "bookSection" : md.title ? "journalArticle" : "",
          title: stripTags(md.title || md.formulaStrippedArticleTitle || ""),
          creators,
          journal: clean(md.publicationTitle || md.displayPublicationTitle || ""),
          date: normalizeDate(md.publicationDate || md.onlineDate || md.dateOfInsertion || ""),
          doi: cleanDoi(md.doi || ""),
          volume: clean(md.volume || ""),
          issue: clean(md.issue || ""),
          pages: pageRange(md.startPage, md.endPage),
          abstract: stripTags(md.abstract || ""),
          publisher: clean(md.publisher || "IEEE"),
          issn,
          isbn,
          tags: splitKeywords((md.keywords || []).flatMap((k) => k.kwd || [])),
          url: arnumber ? `https://ieeexplore.ieee.org/document/${arnumber}` : url,
        },
        pdfCandidates: arnumber ? [
          `https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=${arnumber}&ref=`,
          `https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=${arnumber}`,
        ] : [],
      };
    },
  },
  {
    id: "jstor",
    label: "JSTOR",
    test: (url) => /^https?:\/\/(www\.)?jstor\.org\/stable\//i.test(url),
    run: (doc, url) => {
      const id = (url.match(/stable\/(?:pdf\/)?(\d+)/) || [])[1] || "";
      const doi = cleanDoi(attr(doc, 'meta[name="citation_doi"]', "content")) || doiFromUrl(url) || (id ? `10.2307/${id}` : "");
      return {
        patch: { doi, url: id ? `https://www.jstor.org/stable/${id}` : url, item_type: "journalArticle" },
        pdfCandidates: id ? [`https://www.jstor.org/stable/pdf/${id}.pdf`] : [],
      };
    },
  },
  {
    id: "google-books",
    label: "Google Books",
    test: (url) => /^https?:\/\/(books\.google\.[^/]+\/books|www\.google\.[^/]+\/books\/edition)/i.test(url),
    run: (doc, url) => {
      const idMatch = url.match(/[?&]id=([\w-]+)/) || url.match(/\/edition\/[^/]*\/([\w-]+)/);
      const pageText = clean(doc.body ? doc.body.textContent : "").slice(0, 50000);
      const authors = [...doc.querySelectorAll('[itemprop="author"], a[href*="inauthor"]')].map((a) => a.getAttribute("content") || a.textContent);
      const pub = pageText.match(/Publisher\s*:?\s*([^,\n]{2,80}),\s*(\d{4})/i);
      return {
        skipDoiScan: true,
        patch: {
          item_type: "book",
          title: attr(doc, 'meta[property="og:title"]', "content") || txt(doc, "h1"),
          creators: parseCreators(authors),
          isbn: cleanIsbns((pageText.match(/ISBN\s*:?\s*([\dX\-, ]{10,40})/i) || [])[1] || ""),
          publisher: pub ? clean(pub[1]) : "",
          date: pub ? pub[2] : "",
          abstract: attr(doc, 'meta[property="og:description"]', "content"),
          url: idMatch ? `https://books.google.com/books?id=${idMatch[1]}` : url,
          journal: "",
        },
        pdfCandidates: [],
      };
    },
  },
  {
    id: "wikipedia",
    label: "Wikipedia",
    test: (url) => /^https?:\/\/([a-z\-]+)\.(m\.)?wikipedia\.org\/wiki\//i.test(url),
    run: (doc, url) => {
      const lang = (url.match(/^https?:\/\/([a-z\-]+)\./i) || [])[1] || "";
      const canonical = attr(doc, 'link[rel="canonical"]', "href") || url.replace(/#.*$/, "");
      const modified = (doc.querySelector("#footer-info-lastmod") || {}).textContent || "";
      return {
        skipDoiScan: true,
        patch: {
          item_type: "encyclopediaArticle",
          title: txt(doc, "#firstHeading") || clean(doc.title.replace(/\s+-\s+Wikipedia$/, "")),
          journal: "Wikipedia",
          publisher: "Wikimedia Foundation",
          language: lang,
          url: canonical,
          creators: [],
          date: normalizeDate((modified.match(/(\d{1,2} \w+ \d{4})/) || [])[1] || ""),
          abstract: clean(txt(doc, "#mw-content-text .mw-parser-output > p:not(.mw-empty-elt)")).slice(0, 1200),
        },
        pdfCandidates: [],
      };
    },
  },
  {
    id: "youtube",
    label: "YouTube",
    test: (url) => /^https?:\/\/(www\.|m\.)?youtube\.com\/(watch|shorts\/)|^https?:\/\/youtu\.be\//i.test(url),
    run: (doc, url) => {
      const vid = (url.match(/[?&]v=([\w-]{6,})/) || url.match(/(?:shorts\/|youtu\.be\/)([\w-]{6,})/) || [])[1] || "";
      const channel = attr(doc, 'span[itemprop="author"] link[itemprop="name"], link[itemprop="name"]', "content")
        || txt(doc, "#owner #channel-name a, ytd-channel-name a");
      return {
        skipDoiScan: true,
        patch: {
          item_type: "videoRecording",
          title: attr(doc, 'meta[name="title"]', "content") || attr(doc, 'meta[property="og:title"]', "content") || clean(doc.title.replace(/\s+-\s+YouTube$/, "")),
          creators: channel ? [{ creatorType: "author", name: channel }] : [],
          date: normalizeDate(attr(doc, 'meta[itemprop="uploadDate"], meta[itemprop="datePublished"]', "content")),
          abstract: attr(doc, 'meta[name="description"]', "content"),
          publisher: "YouTube",
          journal: "",
          url: vid ? `https://www.youtube.com/watch?v=${vid}` : url,
        },
        pdfCandidates: [],
      };
    },
  },
  {
    id: "github",
    label: "GitHub",
    test: (url) => /^https?:\/\/github\.com\/[^/?#]+\/[^/?#]+/i.test(url) && !/^https?:\/\/github\.com\/(orgs|settings|topics|marketplace|features|login|search)\//i.test(url),
    run: (doc, url) => {
      const [, owner, repo] = url.match(/^https?:\/\/github\.com\/([^/?#]+)\/([^/?#]+)/i);
      const description = attr(doc, 'meta[name="description"]', "content") || attr(doc, 'meta[property="og:description"]', "content");
      const language = txt(doc, '[itemprop="programmingLanguage"], .BorderGrid .color-fg-default.text-bold');
      return {
        skipDoiScan: true,
        patch: {
          item_type: "computerProgram",
          title: repo.replace(/\.git$/, ""),
          creators: [{ creatorType: "programmer", name: owner }],
          abstract: description.replace(new RegExp(`\\s*-\\s*${owner}/${repo}.*$`), ""),
          publisher: "GitHub",
          journal: "",
          language: "",
          programming_language: language,
          url: `https://github.com/${owner}/${repo.replace(/\.git$/, "")}`,
        },
        pdfCandidates: [],
      };
    },
  },
];

/**
 * Find the translator for a URL.
 *
 * @param {string} url  Page URL.
 * @returns {?object} Translator or null.
 */
export function translatorFor(url) {
  return TRANSLATORS.find((t) => {
    try {
      return t.test(url);
    } catch (_) {
      return false;
    }
  }) || null;
}
