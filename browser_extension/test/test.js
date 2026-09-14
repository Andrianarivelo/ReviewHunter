/**
 * Browser test harness for the extractor and the pure helpers.
 *
 * Run: serve the extension folder over HTTP (fixtures are fetched) and open test/test.html:
 *   python -m http.server 8799 --directory browser_extension
 *   http://127.0.0.1:8799/test/test.html
 * Results are printed on the page and exposed as window.__results.
 *
 * @module test
 */

import { detect, detectPdf, toPayload } from "../src/extract/index.js";
import {
  cleanDoi, doiFromUrl, extractAllDois, arxivIdFrom, pmidFromUrl, piiFromUrl, isLikelyPdfUrl,
  findPdfLinkInHtml, isPdfBytes, identifiersInText, fileNameFromUrl,
} from "../src/shared/identifiers.js";
import { parseCreator, parseCreators, normalizeDate, splitKeywords, cleanIsbns } from "../src/extract/normalize.js";
import { parseKev, kevToPayload } from "../src/extract/coins.js";
import { matchPending, normalizeServerUrl } from "../src/settings.js";

/** @type {{name: string, ok: boolean, detail: string}[]} */
const results = [];
let currentGroup = "";

/**
 * Record an equality assertion (deep compare through JSON).
 *
 * @param {*} actual    Value produced.
 * @param {*} expected  Expected value.
 * @param {string} label  Assertion name.
 * @returns {void}
 */
function eq(actual, expected, label) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  results.push({ name: `${currentGroup}: ${label}`, ok, detail: ok ? "" : `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}` });
}

/**
 * Record a truthiness assertion.
 *
 * @param {*} value  Value that must be truthy.
 * @param {string} label  Assertion name.
 * @param {*} [shown]  Value to display on failure.
 * @returns {void}
 */
function ok(value, label, shown) {
  results.push({ name: `${currentGroup}: ${label}`, ok: !!value, detail: value ? "" : `got ${JSON.stringify(shown === undefined ? value : shown)}` });
}

/**
 * Load a fixture as a Document.
 *
 * @param {string} name  File in fixtures/.
 * @returns {Promise<Document>} Parsed document.
 */
async function fixture(name) {
  const text = await (await fetch(`fixtures/${name}`, { cache: "no-store" })).text();
  return new DOMParser().parseFromString(text, "text/html");
}

/**
 * Run a named group of assertions, catching exceptions as failures.
 *
 * @param {string} name  Group name.
 * @param {() => Promise<void>|void} fn  Body.
 * @returns {Promise<void>}
 */
async function group(name, fn) {
  currentGroup = name;
  try {
    await fn();
  } catch (e) {
    results.push({ name: `${name}: threw`, ok: false, detail: `${e && e.stack ? e.stack : e}` });
  }
}

// ------------------------------------------------------------------------------------------ helpers

await group("identifiers", () => {
  eq(cleanDoi("https://doi.org/10.1016/j.drugalcdep.2017.04.019."), "10.1016/j.drugalcdep.2017.04.019", "doi.org URL + trailing dot");
  eq(cleanDoi("doi:10.1016/0006-8993(82)90101-5"), "10.1016/0006-8993(82)90101-5", "balanced parentheses kept");
  eq(cleanDoi("(see 10.1000/xyz123)"), "10.1000/xyz123", "unbalanced parenthesis stripped");
  eq(doiFromUrl("https://doi.org/10.1038%2Fnature12373"), "10.1038/nature12373", "encoded doi.org");
  eq(doiFromUrl("https://onlinelibrary.wiley.com/doi/epdf/10.1002/hbm.23456"), "10.1002/hbm.23456", "Wiley epdf");
  eq(doiFromUrl("https://www.tandfonline.com/doi/full/10.1080/0954898X.2019.1234567?scroll=top"), "10.1080/0954898X.2019.1234567", "T&F full + query");
  eq(doiFromUrl("https://www.frontiersin.org/articles/10.3389/fncom.2020.00001/full"), "10.3389/fncom.2020.00001", "Frontiers /full suffix");
  eq(doiFromUrl("https://link.springer.com/article/10.1007/s00221-017-4950-8"), "10.1007/s00221-017-4950-8", "Springer path");
  eq(doiFromUrl("https://www.biorxiv.org/content/10.1101/2020.01.01.123456v2.full.pdf"), "10.1101/2020.01.01.123456", "bioRxiv version + .full.pdf");
  eq(doiFromUrl("https://example.com/search?doi=10.5555/abc"), "10.5555/abc", "?doi= parameter");
  eq(doiFromUrl("https://www.sciencedirect.com/science/article/pii/S0376871617302533"), "", "no DOI in SD URL");
  eq(extractAllDois("a 10.1/x b 10.1234/abc, 10.1234/ABC and 10.5678/def."), ["10.1234/abc", "10.5678/def"], "all DOIs, case-insensitive dedupe");
  eq(arxivIdFrom("https://arxiv.org/pdf/1706.03762v7.pdf"), "1706.03762", "arXiv pdf URL");
  eq(arxivIdFrom("arXiv:hep-th/9901001v2"), "hep-th/9901001", "old-style arXiv id");
  eq(pmidFromUrl("https://pubmed.ncbi.nlm.nih.gov/28577389/"), "28577389", "PMID");
  eq(piiFromUrl("https://www.sciencedirect.com/science/article/abs/pii/S0376871617302533"), "S0376871617302533", "PII abs");
  ok(isLikelyPdfUrl("https://www.sciencedirect.com/science/article/pii/S1/pdfft?isDTMRedir=true"), "pdfft is PDF endpoint");
  ok(!isLikelyPdfUrl("https://www.sciencedirect.com/science/article/pii/S1"), "article page is not PDF");
  eq(isPdfBytes(new TextEncoder().encode("%PDF-1.7\n")), true, "PDF magic accepted");
  eq(isPdfBytes(new TextEncoder().encode("<html>login</html>")), false, "HTML rejected");
  eq(isPdfBytes(new Uint8Array([0x25, 0x50])), false, "too short rejected");
  eq(identifiersInText("see 10.1038/nature12373 and arXiv:2008.02217, PMID: 28577389").map((i) => i.type), ["doi", "arxiv", "pmid"], "identifiersInText types");
  eq(fileNameFromUrl("https://arxiv.org/pdf/1706.03762v7"), "1706.03762v7.pdf", "file name gets .pdf");
  eq(fileNameFromUrl("https://www.sciencedirect.com/science/article/pii/S1/pdfft?md5=1&pid=1-s2.0-S1-main.pdf"), "1-s2.0-S1-main.pdf", "file name from pid query");
  eq(normalizeServerUrl("127.0.0.1:8765/"), "http://127.0.0.1:8765", "server URL normalised");
});

await group("interstitial", async () => {
  const html = await (await fetch("fixtures/sd_interstitial.html")).text();
  const link = findPdfLinkInHtml(html, "https://www.sciencedirect.com/science/article/pii/S0376871617302533/pdfft?isDTMRedir=true");
  eq(link, "https://pdf.sciencedirectassets.com/271214/1-s2.0-S0376871617X00065/1-s2.0-S0376871617302533/main.pdf?X-Amz-Security-Token=abc&X-Amz-Date=20260914T000000Z&hash=ff", "meta refresh / JS redirect wins over help link");
  eq(findPdfLinkInHtml('<html><body><a href="/docs/guide.pdf">guide</a> Please sign in</body></html>', "https://idp.example.edu/login", { minScore: 18 }), "", "login page anchor ignored with minScore");
  eq(findPdfLinkInHtml('<iframe src="https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&amp;arnumber=8765432&amp;ref="></iframe>', "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=8765432"),
    "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=8765432&ref=", "IEEE stamp iframe");
  eq(findPdfLinkInHtml('<script>var x = {"pdfUrl":"\\/content\\/paper.pdf"};</script>', "https://pub.example.com/a/1"), "https://pub.example.com/content/paper.pdf", "JSON pdfUrl with escaped slashes");
});

await group("normalize", () => {
  eq(parseCreator("van der Waals, Johannes Diderik"), { creatorType: "author", lastName: "van der Waals", firstName: "Johannes Diderik" }, "Last, First with particle");
  eq(parseCreator("Johannes van der Waals"), { creatorType: "author", lastName: "van der Waals", firstName: "Johannes" }, "First particle Last");
  eq(parseCreator("Auger N"), { creatorType: "author", lastName: "Auger", firstName: "N" }, "PubMed Last Initials");
  eq(parseCreator("World Health Organization"), { creatorType: "author", name: "World Health Organization" }, "organisation kept whole");
  eq(parseCreators("Smith J; Doe A; Smith J").length, 2, "semicolon list deduplicated");
  eq(normalizeDate("12 May 2017"), "2017-05-12", "day month year");
  eq(normalizeDate("2017/5/3"), "2017-05-03", "slashes");
  eq(normalizeDate("May 3, 2017"), "2017-05-03", "US style");
  eq(normalizeDate("2017 Jul"), "2017-07", "PubMed year month");
  eq(normalizeDate("2021-03-15T08:00:00Z"), "2021-03-15", "ISO timestamp");
  eq(splitKeywords("a; b, c; A"), ["a", "b", "c"], "keywords split + dedupe");
  eq(cleanIsbns("ISBN 978-0-262-04199-7"), ["9780262041997"], "ISBN-13");
});

await group("coins-kev", () => {
  const p = kevToPayload(parseKev("rft_val_fmt=info%3Aofi%2Ffmt%3Akev%3Amtx%3Abook&rft.genre=bookitem&rft.atitle=Chapter+One&rft.btitle=Big+Book&rft.au=Doe%2C+Jane&rft.date=2010"));
  eq([p.item_type, p.title, p.journal, p.creators[0].lastName, p.date], ["bookSection", "Chapter One", "Big Book", "Doe", "2010"], "book chapter KEV");
});

await group("pending-match", () => {
  const entry = { itemId: 1, title: "t", doi: "10.1016/j.drugalcdep.2017.04.019", urls: ["https://www.sciencedirect.com/science/article/pii/S0376871617302533/pdfft?isDTMRedir=true"], at: Date.now() };
  eq(matchPending(entry, "https://pdf.sciencedirectassets.com/x/main.pdf?sig=1", ""), 0, "different host family scores 0");
  eq(matchPending(entry, "https://www.sciencedirect.com/science/article/pii/S0376871617302533/pdfft?md5=1", ""), 2, "same path scores 2");
  eq(matchPending(entry, "https://example.org/10.1016/j.drugalcdep.2017.04.019.pdf", "10.1016/J.DRUGALCDEP.2017.04.019"), 3, "same DOI scores 3");
});

// ------------------------------------------------------------------------------------------ fixtures

await group("sciencedirect", async () => {
  const url = "https://www.sciencedirect.com/science/article/pii/S0376871617302533";
  const det = detect(await fixture("sciencedirect.html"), url);
  const it = det.items[0];
  eq([det.kind, det.translator, it.item_type], ["single", "ScienceDirect", "journalArticle"], "kind / translator / type");
  eq(it.doi, "10.1016/j.drugalcdep.2017.04.019", "DOI from citation_doi (not from reference links)");
  eq(it.title, "Association of elevated ambient temperature with opioid overdose deaths", "title");
  eq(it.creators.length, 3, "3 authors");
  eq(it.creators[0], { creatorType: "author", lastName: "Auger", firstName: "Nathalie" }, "first author");
  eq([it.journal, it.volume, it.pages, it.date, it.issn, it.publisher], ["Drug and Alcohol Dependence", "176", "1-8", "2017-07-01", "0376-8716", "Elsevier"], "journal fields");
  eq(it._pdfCandidates[0], "https://www.sciencedirect.com/science/article/pii/S0376871617302533/pdfft?md5=0f1e2d3c4b5a&pid=1-s2.0-S0376871617302533-main.pdf", "signed linkToPdf first");
  ok(it._pdfCandidates.includes("https://www.sciencedirect.com/science/article/pii/S0376871617302533/pdfft?isDTMRedir=true&download=true"), "constructed pdfft fallback", it._pdfCandidates);
  const payload = toPayload(it);
  ok(!Object.keys(payload).some((k) => k.startsWith("_")), "payload has no internal keys", Object.keys(payload));
});

await group("arxiv", async () => {
  const det = detect(await fixture("arxiv.html"), "https://arxiv.org/abs/1706.03762v7");
  const it = det.items[0];
  eq([det.kind, det.translator, it.item_type, it.arxiv_id], ["single", "arXiv", "preprint", "1706.03762"], "arXiv id and preprint");
  eq(it.doi || "", "", "DataCite 10.48550 DOI not used");
  eq(it._pdfCandidates[0], "https://arxiv.org/pdf/1706.03762", "arXiv PDF endpoint first");
  eq([it.date, it.creators[0].lastName, it.url], ["2017-06-12", "Vaswani", "https://arxiv.org/abs/1706.03762"], "date / author / canonical url");
});

await group("pubmed", async () => {
  const det = detect(await fixture("pubmed.html"), "https://pubmed.ncbi.nlm.nih.gov/12345678/");
  const it = det.items[0];
  eq([det.translator, it.pmid, it.doi], ["PubMed", "12345678", "10.5555/envres.2017.0042"], "PMID + DOI");
  eq(it.creators.map((c) => `${c.lastName} ${c.firstName}`), ["Smith J", "Doe AB", "Garcia-Lopez M"], "citation_authors split");
  eq([it.date, it.journal, it.volume], ["2017-07-01", "Environmental Research", "156"], "date/journal/volume");
  eq(it._pdfCandidates[0], "https://pmc.ncbi.nlm.nih.gov/articles/PMC5555555/pdf/", "PMC PDF candidate");
});

await group("jsonld", async () => {
  const det = detect(await fixture("jsonld.html"), "https://journals.example.org/article/42");
  const it = det.items[0];
  eq([det.kind, det.translator, it.item_type], ["single", "Embedded metadata", "journalArticle"], "scholarly article from JSON-LD");
  eq([it.title, it.doi, it.journal, it.volume, it.issue, it.pages, it.issn], ["Grid cells in a synthetic cortex", "10.5555/example.42", "Journal of Examples", "12", "3", "101-120", "1234-5678"], "fields incl. nested isPartOf");
  eq(it.creators, [
    { creatorType: "author", lastName: "Lovelace", firstName: "Ada" },
    { creatorType: "author", lastName: "Cajal", firstName: "Santiago Ramon y" },
    { creatorType: "author", name: "Blue Brain Consortium" },
  ], "person / name / organisation creators");
  eq([it.date, it.abstract, it.tags], ["2021-03-15", "We describe synthetic grid cells.", ["grid cells", "entorhinal cortex"]], "date / abstract stripped / keywords");
  eq(it._pdfCandidates[0], "https://journals.example.org/article/42/pdf/grid-cells.pdf", "PDF from encoding, resolved");
});

await group("scholar", async () => {
  const det = detect(await fixture("scholar.html"), "https://scholar.google.com/scholar?hl=en&q=hopfield");
  eq([det.kind, det.translator, det.items.length], ["multiple", "Google Scholar", 3], "3 results");
  const [a, b, c] = det.items;
  eq([a.doi, a.date, a.pdf_url], ["10.1073/pnas.79.8.2554", "1982", "https://www.pnas.org/doi/pdf/10.1073/pnas.79.8.2554"], "result 1 DOI / year / PDF");
  eq(a.creators[0], { creatorType: "author", lastName: "Hopfield", firstName: "JJ" }, "result 1 author");
  eq([b.title, b.arxiv_id, b.item_type, b.creators.length], ["Hopfield networks is all you need", "2008.02217", "preprint", 4], "result 2 arXiv, badge removed from title");
  eq([c.title, c.url], ["Learning and memory in synthetic attractors", ""], "result 3 citation without link");
});

await group("coins", async () => {
  const det = detect(await fixture("coins.html"), "https://lists.example.org/reading");
  eq([det.kind, det.translator, det.items.length], ["multiple", "COinS", 2], "two COinS");
  const [j, bk] = det.items;
  eq([j.item_type, j.doi, j.journal, j.pages, j.volume, j.creators[0].lastName], ["journalArticle", "10.1073/pnas.79.8.2554", "Proceedings of the National Academy of Sciences", "2554-2558", "79", "Hopfield"], "journal COinS");
  eq([bk.item_type, bk.title, bk.publisher, bk.isbn, bk.creators.length], ["book", "Theoretical Neuroscience", "MIT Press", ["9780262041997"], 2], "book COinS");
});

await group("blog", async () => {
  const det = detect(await fixture("blog.html"), "https://neuronotebook.example.com/2024/05/attractors?utm_source=x");
  const it = det.items[0];
  eq([det.kind, det.scholarly, it.item_type], ["single", false, "blogPost"], "single non-scholarly blog post");
  eq([it.title, it.journal, it.date], ["Why attractor networks still matter", "The Neuro Notebook", "2024-05-02"], "title / site / date");
  eq(it.creators, [{ creatorType: "author", lastName: "Writer", firstName: "Jane Q." }], "author");
  eq(it.doi || "", "", "cited DOI not assigned to the blog post");
  eq(it.url, "https://neuronotebook.example.com/2024/05/attractors", "og:url canonical");
});

await group("doi-list", async () => {
  const det = detect(await fixture("doilist.html"), "https://lab.example.edu/publications");
  eq([det.kind, det.items.length], ["multiple", 3], "3 DOI links");
  eq(det.items.map((i) => i.doi), ["10.1002/hipo.00001", "10.1016/j.neuron.2019.01.001", "10.1111/ejn.99999"], "DOIs in order");
  ok(det.items.every((i) => i._idOnly), "id-only entries");
  eq(det.items[1].title, "Theta sequences revisited", "link text used as title");
  ok(/Place cells in synthetic mazes/.test(det.items[0].title), "list item text used when link is a bare DOI", det.items[0].title);
});

await group("ieee", async () => {
  const det = detect(await fixture("ieee.html"), "https://ieeexplore.ieee.org/document/8765432");
  const it = det.items[0];
  eq([det.translator, it.item_type, it.doi, it.pages], ["IEEE Xplore", "conferencePaper", "10.1109/ICSH.2019.8765432", "10-15"], "conference paper from inline script");
  eq(it.creators.map((c) => c.lastName), ["Hopper", "Turing"], "authors");
  eq(it._pdfCandidates[0], "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=8765432&ref=", "getPDF endpoint first");
  const det2 = detect(await fixture("ieee.html"), "https://ieeexplore.ieee.org/document/8765432", { pageData: { title: "From page world", isJournal: true, articleNumber: "8765432" } });
  eq([det2.items[0].title, det2.items[0].item_type], ["From page world", "journalArticle"], "ctx.pageData overrides inline parse");
});

await group("wiley", async () => {
  const det = detect(await fixture("wiley.html"), "https://onlinelibrary.wiley.com/doi/full/10.1002/hbm.23456");
  const it = det.items[0];
  eq([it.doi, it.issue, it.creators[0].lastName], ["10.1002/hbm.23456", "4", "Kim"], "DOI from URL");
  eq(it._pdfCandidates[0], "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/hbm.23456?download=true", "pdfdirect first");
});

await group("pdf-tab", () => {
  const a = detectPdf("https://arxiv.org/pdf/1706.03762v7");
  eq([a.kind, a.items[0].arxiv_id, a.items[0].item_type, a.items[0].url], ["pdf", "1706.03762", "preprint", "https://arxiv.org/abs/1706.03762"], "arXiv PDF tab");
  const b = detectPdf("https://files.example.org/papers/some_paper-final.pdf");
  eq([b.items[0].item_type, b.items[0].doi, b.items[0].title], ["document", "", "some paper final"], "anonymous PDF");
  const doc = new DOMParser().parseFromString('<html><body><embed type="application/pdf" src="about:blank"></body></html>', "text/html");
  eq(detect(doc, "https://www.jneurosci.org/content/jneuro/37/1/1.full.pdf").kind, "pdf", "Chrome viewer wrapper detected");
});

// ------------------------------------------------------------------------------------------ report

const passed = results.filter((r) => r.ok).length;
const failed = results.length - passed;
window.__results = { passed, failed, total: results.length, failures: results.filter((r) => !r.ok) };
document.getElementById("summary").textContent = `${failed ? "FAIL" : "PASS"}: ${passed}/${results.length} assertions passed`;
document.getElementById("summary").className = failed ? "fail" : "pass";
const ul = document.getElementById("results");
for (const r of results) {
  const li = document.createElement("li");
  li.className = r.ok ? "pass" : "fail";
  li.textContent = `${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.detail ? `  ->  ${r.detail}` : ""}`;
  ul.append(li);
}
