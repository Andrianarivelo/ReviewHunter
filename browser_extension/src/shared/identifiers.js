/**
 * Identifier helpers shared by the extractor (content world), the service worker
 * and the popup. Everything here is pure: no DOM, no chrome.* calls, so the same
 * code runs in a page, in a worker and in the test harness.
 *
 * @module shared/identifiers
 */

/** Global DOI pattern (CrossRef recommendation, relaxed for real-world URLs). */
export const DOI_PATTERN = /\b(10\.\d{4,9}\/[^\s"'<>]+)/gi;

/** Suffixes publishers append after a DOI in their URLs (not part of the DOI). */
const DOI_URL_SUFFIXES = /\/(abstract|full|fulltext|pdf|epdf|pdfdirect|html|references|figures|citedby|supplemental|meta|summary|reader)$/i;

/**
 * Clean a raw DOI candidate: strip resolver prefixes, trailing punctuation,
 * URL query/fragment and publisher path suffixes.
 *
 * @param {string} raw  DOI-like string, possibly a doi.org URL or "doi:10...".
 * @param {{fromUrl?: boolean}} [opts]  fromUrl: the string came from a URL path,
 *        so "?query" and "#hash" and suffixes like "/full" are removed.
 * @returns {string} The cleaned DOI, or "" when it does not look like a DOI.
 */
export function cleanDoi(raw, opts = {}) {
  if (!raw) return "";
  let s = String(raw).trim();
  try {
    if (/%2F|%3A|%28|%29/i.test(s)) s = decodeURIComponent(s);
  } catch (_) {
    /* malformed escape sequence: keep the raw string */
  }
  s = s.replace(/^(?:https?:\/\/)?(?:dx\.)?doi\.org\//i, "").replace(/^doi:\s*/i, "");
  const m = s.match(/10\.\d{4,9}\/\S+/);
  if (!m) return "";
  s = m[0];
  if (opts.fromUrl) {
    s = s.replace(/[?#].*$/, "");
    // Several suffixes can be chained (".../full/pdf"), strip repeatedly.
    let prev;
    do {
      prev = s;
      s = s.replace(DOI_URL_SUFFIXES, "");
    } while (s !== prev);
  }
  // Trailing punctuation from prose or HTML ("...019.", "...019),").
  // A trailing ")" is kept only when balanced by a "(" inside the DOI (e.g. "...(82)").
  let changed = true;
  while (changed) {
    changed = false;
    const t = s.replace(/[.,;:\]}>'"]+$/, "");
    if (t !== s) { s = t; changed = true; }
    if (s.endsWith(")") && (s.match(/\(/g) || []).length < (s.match(/\)/g) || []).length) {
      s = s.slice(0, -1);
      changed = true;
    }
  }
  if (opts.fromUrl) {
    s = s.replace(/\.pdf$/i, "").replace(/\.(full|abstract|supplementary-material)$/i, "");
    // bioRxiv / medRxiv URLs append a version ("10.1101/2020.01.01.123456v2") that is not part of the DOI.
    if (/^10\.1101\/\d{4}\.\d{2}\.\d{2}\.\d+v\d+$/i.test(s)) s = s.replace(/v\d+$/i, "");
  }
  return /^10\.\d{4,9}\/.+/.test(s) ? s : "";
}

/**
 * Find the first DOI in free text.
 *
 * @param {string} text  Arbitrary text.
 * @returns {string} First cleaned DOI or "".
 */
export function extractDoi(text) {
  if (!text) return "";
  DOI_PATTERN.lastIndex = 0;
  const m = DOI_PATTERN.exec(String(text));
  return m ? cleanDoi(m[1]) : "";
}

/**
 * Find every distinct DOI in free text, in order of first appearance.
 *
 * @param {string} text  Arbitrary text.
 * @returns {string[]} Distinct cleaned DOIs (case-insensitive uniqueness).
 */
export function extractAllDois(text) {
  const seen = new Map();
  if (!text) return [];
  for (const m of String(text).matchAll(DOI_PATTERN)) {
    const doi = cleanDoi(m[1]);
    if (doi && !seen.has(doi.toLowerCase())) seen.set(doi.toLowerCase(), doi);
  }
  return [...seen.values()];
}

/**
 * Extract a DOI from a URL: doi.org links, "/doi/10.x" paths (Wiley, T&F, ACS,
 * SAGE), "?doi=" query parameters and any "10.xxxx/..." segment in the path.
 *
 * @param {string} url  Absolute or relative URL.
 * @returns {string} Cleaned DOI or "".
 */
export function doiFromUrl(url) {
  if (!url) return "";
  let u;
  try {
    u = new URL(url, "https://example.invalid/");
  } catch (_) {
    return "";
  }
  const q = u.searchParams.get("doi") || u.searchParams.get("DOI");
  if (q) return cleanDoi(q);
  if (/(^|\.)doi\.org$/i.test(u.hostname)) return cleanDoi(decodeSafe(u.pathname.slice(1)), { fromUrl: true });
  const path = decodeSafe(u.pathname);
  const m = path.match(/\/doi\/(?:(?:abs|full|pdf|pdfdirect|epdf|epub|book|chapter|reader|figure|suppl)\/)?(10\.\d{4,9}\/.+)$/i)
    || path.match(/(10\.\d{4,9}\/.+)$/);
  return m ? cleanDoi(m[1], { fromUrl: true }) : "";
}

/**
 * decodeURIComponent that never throws.
 *
 * @param {string} s  Possibly percent-encoded string.
 * @returns {string} Decoded string (or the input on failure).
 */
function decodeSafe(s) {
  try {
    return decodeURIComponent(s);
  } catch (_) {
    return s;
  }
}

/**
 * Extract an arXiv identifier (new "2101.01234" or old "hep-th/9901001" style)
 * from an arxiv.org URL (abs, pdf, html) or an "arXiv:" string.
 *
 * @param {string} s  URL or text.
 * @returns {string} Bare arXiv id without version, or "".
 */
export function arxivIdFrom(s) {
  if (!s) return "";
  const str = String(s);
  const m = str.match(/arxiv\.org\/(?:abs|pdf|html|format)\/((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?\/\d{7}))(?:v\d+)?/i)
    || str.match(/arXiv:\s*((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?\/\d{7}))(?:v\d+)?/i);
  return m ? m[1] : "";
}

/**
 * Extract a PubMed id from a PubMed URL.
 *
 * @param {string} url  URL.
 * @returns {string} PMID or "".
 */
export function pmidFromUrl(url) {
  const m = String(url || "").match(/(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov\/pubmed)\/(\d{1,9})(?:[/?#]|$)/i);
  return m ? m[1] : "";
}

/**
 * Extract a PMC id ("PMC1234567") from a PMC URL.
 *
 * @param {string} url  URL.
 * @returns {string} PMCID or "".
 */
export function pmcidFromUrl(url) {
  const m = String(url || "").match(/\/(?:pmc\/)?articles\/(PMC\d+)/i);
  return m ? m[1].toUpperCase() : "";
}

/**
 * Extract an Elsevier PII from a ScienceDirect URL.
 *
 * @param {string} url  URL.
 * @returns {string} PII (e.g. "S0376871617302296") or "".
 */
export function piiFromUrl(url) {
  const m = String(url || "").match(/sciencedirect\.com\/science\/article\/(?:abs\/)?pii\/([A-Z0-9]+)/i);
  return m ? m[1] : "";
}

/**
 * Heuristic: does this URL most likely point at a PDF file or a PDF endpoint?
 *
 * @param {string} url  URL.
 * @returns {boolean} True for ".pdf" paths and well-known PDF endpoints.
 */
export function isLikelyPdfUrl(url) {
  if (!url) return false;
  let u;
  try {
    u = new URL(url);
  } catch (_) {
    return false;
  }
  const p = u.pathname.toLowerCase();
  return p.endsWith(".pdf")
    || /arxiv\.org$/i.test(u.hostname) && p.startsWith("/pdf/")
    || /\/doi\/(pdf|pdfdirect|epdf)\//.test(p)
    || /\/pdfft$/.test(p)
    || /\/content\/pdf\//.test(p)
    || /stamp(pdf)?\/(stamp|getpdf)\.jsp$/.test(p)
    || /\/stable\/pdf\//.test(p);
}

/**
 * Identify every identifier (DOI, arXiv, PMID with explicit prefix) in a text.
 * Used for "Add selection as identifiers" and for classifying links.
 *
 * @param {string} text  Text or URL.
 * @returns {{type: "doi"|"arxiv"|"pmid", value: string}[]} Found identifiers.
 */
export function identifiersInText(text) {
  const out = [];
  const s = String(text || "");
  for (const doi of extractAllDois(s)) out.push({ type: "doi", value: doi });
  const seenArxiv = new Set();
  for (const m of s.matchAll(/(?:arxiv\.org\/(?:abs|pdf)\/|arXiv:\s*)((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?\/\d{7}))/gi)) {
    if (!seenArxiv.has(m[1])) {
      seenArxiv.add(m[1]);
      out.push({ type: "arxiv", value: m[1] });
    }
  }
  const pmid = pmidFromUrl(s) || (s.match(/\bPMID:?\s*(\d{5,9})\b/i) || [])[1];
  if (pmid) out.push({ type: "pmid", value: pmid });
  return out;
}

/**
 * Decode the handful of HTML entities that appear inside attribute values.
 *
 * @param {string} s  Raw attribute text.
 * @returns {string} Decoded text.
 */
export function decodeEntities(s) {
  return String(s || "")
    .replace(/&amp;/g, "&")
    .replace(/&#x2F;/gi, "/")
    .replace(/&#47;/g, "/")
    .replace(/&#x3D;/gi, "=")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

/**
 * Parse an HTML document (as text, no DOM needed) returned instead of a PDF and
 * look for the real PDF location. Handles the interstitial pages used by
 * ScienceDirect ("pdfft" redirect page), IEEE stamp pages (iframe), meta refresh,
 * JavaScript redirects, embedded viewers and plain download links.
 *
 * Scores: meta refresh 30, JS redirect 25, viewer iframe/embed 20 (5 without "pdf"),
 * citation_pdf_url 18, JSON pdf keys 15, plain anchors 5; +10 when the URL itself
 * looks like a PDF. Callers can require a minimum score so that an ordinary
 * login page linking to some unrelated ".pdf" is not mistaken for an interstitial.
 *
 * @param {string} html     HTML source.
 * @param {string} baseUrl  URL the HTML was served from (for relative links).
 * @param {{minScore?: number}} [opts]  minScore: ignore weaker candidates (default 0).
 * @returns {string} Absolute URL of the most plausible PDF, or "".
 */
export function findPdfLinkInHtml(html, baseUrl, opts = {}) {
  if (!html) return "";
  const src = String(html).slice(0, 2_000_000);
  const candidates = [];
  const push = (raw, score) => {
    if (!raw) return;
    let v = decodeEntities(raw.trim()).replace(/\\\//g, "/").replace(/\\u0026/gi, "&");
    if (!v || v.startsWith("javascript:") || v.startsWith("#")) return;
    try {
      v = new URL(v, baseUrl).href;
    } catch (_) {
      return;
    }
    if (v === baseUrl) return;
    candidates.push({ url: v, score: score + (isLikelyPdfUrl(v) ? 10 : 0) });
  };
  // 1. <meta http-equiv="refresh" content="0; url=...">
  for (const m of src.matchAll(/<meta[^>]+http-equiv=["']?refresh["']?[^>]*>/gi)) {
    const c = m[0].match(/content=["'][^"']*?url=['"]?([^"'>]+)/i);
    if (c) push(c[1], 30);
  }
  // 2. JavaScript redirects: window.location = '...', location.href="...", location.replace('...').
  for (const m of src.matchAll(/(?:window\.|document\.|top\.)?location(?:\.href)?\s*(?:=|\.replace\(|\.assign\()\s*["']([^"']+)["']/gi)) {
    push(m[1], 25);
  }
  // 3. Viewers: <iframe|embed|object src|data="...pdf...">.
  for (const m of src.matchAll(/<(?:iframe|embed|object)[^>]+(?:src|data)=["']([^"']+)["']/gi)) {
    push(m[1], /pdf/i.test(m[1]) ? 20 : 5);
  }
  // 4. citation_pdf_url inside the interstitial.
  for (const m of src.matchAll(/<meta[^>]+name=["']citation_pdf_url["'][^>]*content=["']([^"']+)["']/gi)) push(m[1], 18);
  // 5. JSON blobs: "pdfUrl":"...", "linkToPdf":"...", "downloadUrl":"....pdf".
  for (const m of src.matchAll(/"(?:pdfUrl|pdf_url|linkToPdf|downloadUrl|pdfLink)"\s*:\s*"([^"]+)"/gi)) push(m[1], 15);
  // 6. Anchors pointing at PDFs (href containing .pdf or pdf endpoints).
  for (const m of src.matchAll(/<a\b[^>]*href=["']([^"']+)["'][^>]*>/gi)) {
    if (/\.pdf(\b|$)|pdfft|\/pdf\/|getPDF|pdfdirect|download=true/i.test(m[1])) push(m[1], 5);
  }
  const ok = candidates.filter((c) => c.score >= (opts.minScore || 0));
  if (!ok.length) return "";
  ok.sort((a, b) => b.score - a.score);
  return ok[0].url;
}

/**
 * Is this HTML a human-verification wall (ALTCHA "are you a robot?", DDoS-Guard,
 * Cloudflare, "Antibot" loading pages) rather than real content? Only the first
 * 20 kB are inspected: markers live in the first screen, while a real article
 * could mention "robot" anywhere in its text. Mirrors sources.is_bot_challenge.
 *
 * @param {string} html  HTML source.
 * @returns {boolean} True for challenge pages.
 */
export function isBotChallengeHtml(html) {
  return classifyWall(html) !== null;
}

/** Walls a real browser passes on its own (JavaScript checks), with a label for progress lines. */
const AUTO_WALLS = [
  [/just a moment\.\.\.|cf-chl|challenge-platform|cf-challenge|_cf_chl_opt/i, "Cloudflare browser check"],
  [/ddos-guard/i, "DDoS-Guard browser check"],
  [/preparing to download/i, "NCBI download page"],
  [/recaptcha\/challengepage|checking your browser - recaptcha/i, "reCAPTCHA browser check"],
  [/antibot solution|<title[^>]*>\s*loading\.\.\.\s*<\/title>/i, "antibot loading page"],
  [/checking your browser|checking if the site connection is secure/i, "browser check"],
];

/**
 * Classify a wall page. Mirrors transport.classify_challenge on the server.
 *
 * @param {string} html  HTML source (only the first 20 kB are inspected).
 * @returns {?{kind: "auto"|"interactive"|"blocked", label: string}} Null for real content.
 *   auto: clears by itself in a tab (Cloudflare, DDoS-Guard, NCBI cookie page, invisible reCAPTCHA);
 *   interactive: the user must act (ALTCHA "are you a robot", hCaptcha);
 *   blocked: a web-filter block page (FortiGuard "Site bloque", "Web Page Blocked").
 */
export function classifyWall(html) {
  const head = String(html || "").slice(0, 20000);
  if (!head.trim()) return null;
  const titleMatch = head.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const title = titleMatch ? titleMatch[1].replace(/\s+/g, " ").trim() : "";
  const blockRx = /site bloqu|web page blocked|fortiguard|fortinet|access to this site is blocked|this website is blocked|url blocked|blocked by your (?:organization|administrator)/i;
  if (blockRx.test(title) || (blockRx.test(head) && /fortinet/i.test(head))) {
    return { kind: "blocked", label: `web filter block page${title ? ` (${title})` : ""}` };
  }
  if (/altcha|êtes-vous un robot|etes-vous un robot|are you a robot|hcaptcha|verify you are human|confirm you are human|press and hold|slide to verify/i.test(head)) {
    return { kind: "interactive", label: "human verification (ALTCHA / CAPTCHA)" };
  }
  for (const [rx, label] of AUTO_WALLS) if (rx.test(head)) return { kind: "auto", label };
  return null;
}

/**
 * Is this URL a Sci-Hub mirror or an Anna's Archive SciDB page?
 *
 * @param {string} url  URL.
 * @returns {"scihub"|"scidb"|""} Library kind, or "" for any other site.
 */
export function shadowLibraryKind(url) {
  let u;
  try {
    u = new URL(url);
  } catch (_) {
    return "";
  }
  if (/(^|\.)sci-hub\.[a-z]{2,}$/i.test(u.hostname)) return "scihub";
  if (/(^|\.)annas-archive\.[a-z]{2,}$/i.test(u.hostname) && /^\/scidb\//i.test(u.pathname)) return "scidb";
  return "";
}

/**
 * Check whether a byte buffer is a PDF (starts with the "%PDF-" magic number).
 * The server applies the exact same rule, so this avoids uploading login pages.
 *
 * @param {Uint8Array|ArrayBuffer} bytes  Buffer (only the first 5 bytes are read).
 * @returns {boolean} True when the buffer starts with %PDF-.
 */
export function isPdfBytes(bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || new ArrayBuffer(0));
  return b.length >= 5 && b[0] === 0x25 && b[1] === 0x50 && b[2] === 0x44 && b[3] === 0x46 && b[4] === 0x2d;
}

/**
 * Derive a human-friendly file name from a URL ("paper.pdf" style).
 *
 * @param {string} url  URL.
 * @param {string} [fallback="document.pdf"]  Used when the URL has no usable name.
 * @returns {string} File name ending in .pdf.
 */
export function fileNameFromUrl(url, fallback = "document.pdf") {
  try {
    const u = new URL(url);
    // Endpoints like ".../pdfft?pid=1-s2.0-S0376871617302533-main.pdf" carry the real name in the query.
    const fromQuery = [...u.searchParams.values()].find((v) => /^[^/?#]{3,}\.pdf$/i.test(v));
    const last = decodeSafe(fromQuery || u.pathname.split("/").filter(Boolean).pop() || "");
    if (!last) return fallback;
    const base = last.replace(/[^\w.\- ]+/g, "_").slice(0, 120);
    return /\.pdf$/i.test(base) ? base : `${base}.pdf`;
  } catch (_) {
    return fallback;
  }
}
