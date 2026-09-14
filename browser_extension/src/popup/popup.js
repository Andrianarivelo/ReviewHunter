/**
 * Popup controller: shows what was detected on the active tab, lets the user pick
 * a collection, tags, a note and options, then asks the service worker to save
 * (progress streamed back through a port).
 *
 * @module popup
 */

import * as api from "../api.js";
import { getSettings } from "../settings.js";
import { yearOf } from "../extract/normalize.js";

/** Short labels for Zotero item types. */
const TYPE_LABELS = {
  journalArticle: "Journal article", book: "Book", bookSection: "Book section", conferencePaper: "Conference paper",
  preprint: "Preprint", thesis: "Thesis", report: "Report", webpage: "Web page", blogPost: "Blog post",
  magazineArticle: "Magazine article", newspaperArticle: "Newspaper article", dataset: "Dataset",
  computerProgram: "Software", patent: "Patent", presentation: "Presentation", document: "Document",
  encyclopediaArticle: "Encyclopedia article", videoRecording: "Video", forumPost: "Forum post", podcast: "Podcast",
};

/** Shorthand for getElementById. @param {string} id @returns {HTMLElement} */
const $ = (id) => document.getElementById(id);

/** Popup state. */
const state = {
  tab: null,
  settings: null,
  detection: null,
  collections: [],
  tags: [],
  selectedTags: [],
  collectionId: null,
  selected: new Set(),
  saving: false,
  savedIds: [],
};

// ------------------------------------------------------------------------------------------
// Utilities
// ------------------------------------------------------------------------------------------

/**
 * Send a message to the service worker.
 *
 * @param {object} msg  Message with a `type`.
 * @returns {Promise<any>} Response (rejects when the worker returns {__rhError}).
 */
async function send(msg) {
  const res = await chrome.runtime.sendMessage(msg);
  if (res && res.__rhError) throw new Error(res.__rhError);
  return res;
}

/**
 * Show exactly one of the main state sections.
 *
 * @param {string} name  Section suffix: loading | offline | none | single | multi | pdf | progress.
 * @returns {void}
 */
function showState(name) {
  for (const s of document.querySelectorAll("main > .state")) s.hidden = s.id !== `state-${name}`;
  const editable = ["single", "multi", "pdf"].includes(name);
  $("options").hidden = !editable;
  $("footer").hidden = !editable;
}

/**
 * Create an element with properties and children.
 *
 * @param {string} tag  Tag name.
 * @param {object} [props]  Properties (className, textContent, dataset, on* handlers...).
 * @param {...(Node|string)} children  Children.
 * @returns {HTMLElement} Element.
 */
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el) el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const c of children) if (c !== null && c !== undefined) el.append(c);
  return el;
}

/**
 * Inline SVG <use> reference.
 *
 * @param {string} id  Symbol id without "#".
 * @returns {SVGElement} SVG element.
 */
function icon(id) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
}

/**
 * "Hopfield, Tank & 3 others" style author summary.
 *
 * @param {object[]} creators  Creators.
 * @returns {string} Summary.
 */
function authorsShort(creators) {
  const names = (creators || []).filter((c) => !c.creatorType || c.creatorType === "author" || c.creatorType === "programmer")
    .map((c) => c.name || [c.firstName, c.lastName].filter(Boolean).join(" "));
  if (!names.length) return "";
  if (names.length <= 3) return names.join(", ");
  return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}

/**
 * Autosize a textarea to its content.
 *
 * @param {HTMLTextAreaElement} ta  Textarea.
 * @returns {void}
 */
function autosize(ta) {
  ta.style.height = "auto";
  ta.style.height = `${Math.min(ta.scrollHeight + 2, 140)}px`;
}

// ------------------------------------------------------------------------------------------
// Boot
// ------------------------------------------------------------------------------------------

/**
 * Initialise the popup: connection check, detection and option data in parallel.
 *
 * @param {{force?: boolean}} [opts]  force: re-ping even if the cached status is fresh.
 * @returns {Promise<void>}
 */
async function init(opts = {}) {
  showState("loading");
  state.settings = await getSettings();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  state.tab = tab;

  let status = await send({ type: "status", force: !!opts.force });
  if (!status.online && !opts.force) status = await send({ type: "status", force: true });
  setConnection(status);
  if (!status.online) {
    $("offline-url").textContent = state.settings.serverUrl;
    showState("offline");
    return;
  }

  const [det, collections, tags] = await Promise.all([
    tab ? send({ type: "detect", tabId: tab.id }).catch((e) => ({ kind: "none", items: [], error: e.message })) : { kind: "none", items: [] },
    api.getCollections().catch(() => []),
    api.getTags().catch(() => []),
  ]);
  state.detection = det;
  state.collections = collections;
  state.tags = tags;

  // lastCollectionId: null = never saved yet (use the default), 0 = library root, n = collection id.
  const remembered = state.settings.lastCollectionId ?? state.settings.defaultCollectionId;
  state.collectionId = collections.some((c) => c.id === remembered) ? remembered : null;
  $("opt-pdf").checked = !!state.settings.attachPdf;
  $("opt-enrich").checked = !!state.settings.improveMetadata;
  renderCollectionButton();

  if (det.kind === "single") renderSingle(det);
  else if (det.kind === "multiple") renderMultiple(det);
  else if (det.kind === "pdf") await renderPdf(det);
  else {
    $("none-reason").textContent = det.error || "No reference could be detected on this page.";
    showState("none");
  }
}

/**
 * Update the connection indicator in the header.
 *
 * @param {{online: boolean, info?: object}} status  Status.
 * @returns {void}
 */
function setConnection(status) {
  const el = $("conn");
  el.classList.toggle("online", !!status.online);
  el.classList.toggle("offline", !status.online);
  $("conn-text").textContent = status.online ? `${status.info.library.items} items` : "Offline";
  el.title = status.online ? `ReviewHunter ${status.info.version} at ${state.settings.serverUrl}` : "ReviewHunter is not reachable";
}

// ------------------------------------------------------------------------------------------
// Renderers
// ------------------------------------------------------------------------------------------

/**
 * Render a single detected item.
 *
 * @param {object} det  Detection.
 * @returns {void}
 */
function renderSingle(det) {
  const it = det.items[0];
  $("single-type").textContent = TYPE_LABELS[it.item_type] || it.item_type;
  $("single-source").textContent = det.translator || "";
  const ta = $("single-title");
  ta.value = it.title || "";
  $("single-authors").textContent = authorsShort(it.creators) || "No authors detected";
  $("single-authors").classList.toggle("muted", !it.creators || !it.creators.length);
  $("single-venue").textContent = [yearOf(it.date), it.journal].filter(Boolean).join(" · ");
  $("single-doi").hidden = !it.doi;
  $("single-doi").textContent = it.doi ? `doi:${it.doi}` : "";
  $("single-doi").title = det.doiVia && det.doiVia !== "metadata" ? `DOI found via ${det.doiVia}` : "DOI";
  const hasPdf = !!(it._pdfCandidates && it._pdfCandidates.length);
  $("single-pdf").hidden = !hasPdf;
  $("sw-enrich").hidden = !(it.doi || it.arxiv_id || it.pmid);
  $("save-label").textContent = "Save to ReviewHunter";
  $("single-challenge").hidden = !det.challenge;
  showState("single");
  requestAnimationFrame(() => autosize(ta));
  api.lookup({ doi: it.doi || undefined, url: it.url || undefined, title: it.title || undefined })
    .then((r) => {
      showInLibrary($("single-inlib"), r);
      if (r.found) $("save-label").textContent = "Update in ReviewHunter";
    })
    .catch(() => {});
}

/**
 * Show whether a reference is already saved, and with or without its PDF.
 *
 * @param {HTMLElement} el  Badge element.
 * @param {{found: boolean, items: Array<{has_pdf?: boolean}>}} r  Lookup result.
 * @returns {void}
 */
function showInLibrary(el, r) {
  el.hidden = !r.found;
  if (!r.found) return;
  const hasPdf = r.items.some((i) => i.has_pdf);
  el.className = `badge ${hasPdf ? "ok" : "warn"}`;
  el.replaceChildren(icon(hasPdf ? "i-check" : "i-minus"), hasPdf ? "Saved · PDF" : "Saved · no PDF");
  el.title = hasPdf ? "Already in your library with its PDF" : "Already in your library, but no PDF is attached yet";
}

/**
 * Render a list of detected items with checkboxes.
 *
 * @param {object} det  Detection.
 * @returns {void}
 */
function renderMultiple(det) {
  const list = $("multi-list");
  list.replaceChildren();
  state.selected = new Set();
  det.items.forEach((it, idx) => {
    const cb = h("input", { type: "checkbox", onchange: () => toggleSelected(idx, cb.checked) });
    const sub = [authorsShort(it.creators), yearOf(it.date), it.journal].filter(Boolean).join(" · ") || (it.doi ? `doi:${it.doi}` : it.url || "");
    const li = h("li", { role: "option", dataset: { idx: String(idx) } },
      h("label", { className: "check", onclick: (e) => e.stopPropagation() }, cb, h("span")),
      h("div", { className: "mi-body" },
        h("div", { className: "mi-title", textContent: it.title || it.doi || "Untitled" }),
        h("div", { className: "mi-sub" }, sub, it.pdf_url ? h("span", { className: "mi-pdf", textContent: " · PDF" }) : null)));
    li.addEventListener("click", () => {
      cb.checked = !cb.checked;
      toggleSelected(idx, cb.checked);
    });
    list.append(li);
  });
  $("multi-count").textContent = `${det.items.length} reference${det.items.length > 1 ? "s" : ""}`;
  $("multi-source").textContent = det.translator ? `· ${det.translator}` : "";
  $("sw-enrich").hidden = !det.items.some((i) => !i._idOnly);
  updateSelectionUi();
  showState("multi");
}

/**
 * Toggle one entry of the multi list.
 *
 * @param {number} idx  Item index.
 * @param {boolean} on  Selected.
 * @returns {void}
 */
function toggleSelected(idx, on) {
  if (on) state.selected.add(idx);
  else state.selected.delete(idx);
  updateSelectionUi();
}

/**
 * Sync the "select all" checkbox, the counter and the save button.
 *
 * @returns {void}
 */
function updateSelectionUi() {
  const total = state.detection.items.length;
  const n = state.selected.size;
  const all = $("multi-all");
  all.checked = n === total && total > 0;
  all.indeterminate = n > 0 && n < total;
  $("multi-selected").textContent = n ? `${n} selected` : "";
  $("save-label").textContent = n ? `Save ${n} item${n > 1 ? "s" : ""}` : "Select items to save";
  $("btn-save").disabled = n === 0;
}

/**
 * Render a PDF tab, with suggestions to attach it to items still waiting for a PDF.
 *
 * @param {object} det  Detection.
 * @returns {Promise<void>}
 */
async function renderPdf(det) {
  const it = det.items[0];
  $("pdf-name").textContent = it.title || "PDF document";
  $("pdf-doi").hidden = !(it.doi || it.arxiv_id);
  $("pdf-doi").textContent = it.doi ? `doi:${it.doi}` : it.arxiv_id ? `arXiv:${it.arxiv_id}` : "";
  $("pdf-hint").textContent = it.doi || it.arxiv_id ? "Metadata will be resolved from the identifier" : "Metadata will be read from the PDF";
  $("sw-pdf").hidden = true;
  $("sw-enrich").hidden = true;
  $("save-label").textContent = "Save PDF";
  showState("pdf");
  if (it.doi) api.lookup({ doi: it.doi }).then((r) => showInLibrary($("pdf-inlib"), r)).catch(() => {});

  const pending = await send({ type: "pending", url: det.url, doi: it.doi }).catch(() => []);
  const list = $("pending-list");
  list.replaceChildren();
  pending.slice(0, 4).forEach((p, i) => {
    const btn = h("button", { className: `btn${i === 0 && p.score > 0 ? " primary" : ""}`, type: "button", textContent: "Attach" });
    btn.addEventListener("click", () => startAttachPending(p));
    list.append(h("li", { className: i === 0 && p.score > 0 ? "best" : "", title: p.title },
      h("span", { className: "p-title", textContent: p.title }), btn));
  });
  $("pending-box").hidden = !pending.length;
  // A matching entry (same DOI, URL or site) is highlighted first: "Attach to <title>".
  $("pending-box").querySelector(".pending-head").lastChild.textContent = pending.length && pending[0].score > 0
    ? "Attach this PDF to a saved item?" : "Items waiting for their PDF";
}

// ------------------------------------------------------------------------------------------
// Collection picker
// ------------------------------------------------------------------------------------------

/**
 * Show the selected collection in the picker button.
 *
 * @returns {void}
 */
function renderCollectionButton() {
  const c = state.collections.find((x) => x.id === state.collectionId);
  $("collection-label").textContent = c ? c.path : "My Library";
  $("collection-ico").setAttribute("href", c ? "#i-folder" : "#i-library");
}

/**
 * Open or close the collection dropdown.
 *
 * @param {boolean} open  Desired state.
 * @returns {void}
 */
function toggleCollectionPanel(open) {
  $("collection-panel").hidden = !open;
  $("collection-btn").setAttribute("aria-expanded", String(open));
  if (open) {
    $("collection-search").value = "";
    renderCollectionList();
    $("collection-search").focus();
  }
}

/**
 * Render the collection list filtered by the search box (tree view when not filtering).
 *
 * @returns {void}
 */
function renderCollectionList() {
  const q = $("collection-search").value.trim().toLowerCase();
  const list = $("collection-list");
  list.replaceChildren();
  const rows = [{ id: null, name: "My Library", path: "My Library", depth: 0, count: null }, ...state.collections]
    .filter((c) => !q || c.path.toLowerCase().includes(q));
  if (!rows.length) {
    list.append(h("li", { className: "dd-empty", textContent: "No matching collection" }));
    return;
  }
  rows.forEach((c, i) => {
    const li = h("li", { role: "option", className: `${c.id === state.collectionId ? "selected" : ""}${i === 0 ? " active" : ""}`, dataset: { id: c.id === null ? "" : String(c.id) } },
      icon(c.id === null ? "i-library" : "i-folder"),
      h("span", { className: "dd-name", textContent: q ? c.path : c.name, title: c.path }),
      c.count !== null && c.count !== undefined ? h("span", { className: "dd-count", textContent: String(c.count) }) : null);
    li.style.paddingLeft = `${8 + (q ? 0 : c.depth * 14)}px`;
    li.addEventListener("click", () => chooseCollection(c.id));
    li.addEventListener("mousemove", () => setActive(list, li));
    list.append(li);
  });
}

/**
 * Highlight one dropdown row.
 *
 * @param {HTMLElement} list  List element.
 * @param {HTMLElement} li    Row to activate.
 * @returns {void}
 */
function setActive(list, li) {
  for (const x of list.querySelectorAll("li.active")) x.classList.remove("active");
  if (li) {
    li.classList.add("active");
    li.scrollIntoView({ block: "nearest" });
  }
}

/**
 * Move the active row with the keyboard.
 *
 * @param {HTMLElement} list  List.
 * @param {number} delta  +1 / -1.
 * @returns {void}
 */
function moveActive(list, delta) {
  const rows = [...list.querySelectorAll("li:not(.dd-empty)")];
  if (!rows.length) return;
  const i = rows.findIndex((r) => r.classList.contains("active"));
  setActive(list, rows[(i + delta + rows.length) % rows.length]);
}

/**
 * Select a collection and close the dropdown.
 *
 * @param {?number} id  Collection id (null = library root).
 * @returns {void}
 */
function chooseCollection(id) {
  state.collectionId = id;
  renderCollectionButton();
  toggleCollectionPanel(false);
  $("collection-btn").focus();
}

// ------------------------------------------------------------------------------------------
// Tags
// ------------------------------------------------------------------------------------------

/**
 * Add a tag chip (case-insensitive de-duplication).
 *
 * @param {string} name  Tag.
 * @returns {void}
 */
function addTag(name) {
  const t = name.trim().replace(/,$/, "");
  if (!t || state.selectedTags.some((x) => x.toLowerCase() === t.toLowerCase())) return;
  state.selectedTags.push(t);
  renderTags();
}

/**
 * Render tag chips.
 *
 * @returns {void}
 */
function renderTags() {
  const box = $("tag-chips");
  box.replaceChildren();
  for (const t of state.selectedTags) {
    const known = state.tags.find((x) => x.name.toLowerCase() === t.toLowerCase());
    const rm = h("button", { type: "button", title: `Remove ${t}` }, icon("i-x"));
    rm.addEventListener("click", (e) => {
      e.stopPropagation();
      state.selectedTags = state.selectedTags.filter((x) => x !== t);
      renderTags();
    });
    const dot = known && known.color ? h("span", { className: "dotc" }) : null;
    if (dot) dot.style.background = known.color;
    box.append(h("span", { className: "tag-chip" }, dot, t, rm));
  }
}

/**
 * Render tag suggestions for the current input.
 *
 * @returns {void}
 */
function renderTagSuggestions() {
  const q = $("tag-input").value.trim().toLowerCase();
  const list = $("tag-suggest");
  list.replaceChildren();
  if (!q) {
    list.hidden = true;
    return;
  }
  const taken = new Set(state.selectedTags.map((t) => t.toLowerCase()));
  const matches = state.tags
    .filter((t) => !taken.has(t.name.toLowerCase()) && t.name.toLowerCase().includes(q))
    .sort((a, b) => (b.name.toLowerCase().startsWith(q) - a.name.toLowerCase().startsWith(q)) || b.count - a.count)
    .slice(0, 7);
  matches.forEach((t, i) => {
    const li = h("li", { role: "option", className: i === 0 ? "active" : "", dataset: { name: t.name } },
      h("span", { className: "dd-name", textContent: t.name }), h("span", { className: "dd-count", textContent: String(t.count) }));
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      addTag(t.name);
      $("tag-input").value = "";
      renderTagSuggestions();
    });
    list.append(li);
  });
  list.hidden = !matches.length;
}

// ------------------------------------------------------------------------------------------
// Save
// ------------------------------------------------------------------------------------------

/**
 * Collect the form into a save request and run it in the service worker.
 *
 * @returns {void}
 */
function startSave() {
  if (state.saving || !state.detection) return;
  const det = state.detection;
  if (det.kind === "multiple" && !state.selected.size) return;
  // Pending text in the tag box counts as a tag.
  if ($("tag-input").value.trim()) addTag($("tag-input").value);
  const request = {
    tabId: state.tab ? state.tab.id : null,
    detection: det,
    selected: [...state.selected].sort((a, b) => a - b),
    overrides: det.kind === "single" && $("single-title").value.trim() ? { title: $("single-title").value.trim() } : {},
    collectionId: state.collectionId,
    tags: state.selectedTags,
    note: $("note").hidden ? "" : $("note").value.trim(),
    attachPdf: $("opt-pdf").checked,
    enrich: $("opt-enrich").checked,
    snapshot: !!state.settings.saveSnapshot,
  };
  runPort("save", request);
}

/**
 * Attach the current PDF tab to an item saved earlier.
 *
 * @param {{itemId: number, title: string}} pending  Pending entry.
 * @returns {void}
 */
function startAttachPending(pending) {
  if (state.saving) return;
  runPort("attachPending", { tabId: state.tab.id, url: state.detection.url, itemId: pending.itemId, title: pending.title });
}

/**
 * Open the save port, stream progress into the checklist and show the final actions.
 *
 * @param {"save"|"attachPending"} type  Request type.
 * @param {object} request  Request payload.
 * @returns {void}
 */
function runPort(type, request) {
  state.saving = true;
  $("progress-title").textContent = type === "save" ? "Saving to ReviewHunter" : "Attaching PDF";
  $("steps").replaceChildren();
  $("progress-actions").hidden = true;
  $("result").hidden = true;
  showState("progress");
  const port = chrome.runtime.connect({ name: "rh-save" });
  port.onMessage.addListener(async (msg) => {
    if (msg.type === "progress") renderSteps(msg.steps);
    if (msg.type === "done") {
      state.savedIds = msg.result.itemIds || [];
      port.disconnect();
      const hadError = [...$("steps").children].some((li) => li.classList.contains("error"));
      $("progress-title").textContent = msg.result.ok ? "Checking the library" : "Not saved";
      await showVerifiedResult(msg.result, hadError, type);
      state.saving = false;
      $("progress-actions").hidden = false;
      $("btn-open-app").hidden = !state.savedIds.length;
      $("btn-done").focus();
    }
  });
  port.onDisconnect.addListener(() => {
    if (state.saving) {
      state.saving = false;
      $("progress-title").textContent = "Connection to the extension was lost";
      $("progress-actions").hidden = false;
    }
  });
  port.postMessage({ type, request });
}

/**
 * Ask ReviewHunter what was really stored and show it as a banner: green when every item is
 * saved with a PDF, amber when saved without PDF (or the PDF is on its way), red when the
 * server cannot confirm. A save is never reported as done from the extension's side alone.
 *
 * @param {{ok: boolean, itemIds: number[], message: string, watching?: boolean}} result  Save summary.
 * @param {boolean} hadError  A step reported an error.
 * @param {"save"|"attachPending"} type  Request type.
 * @returns {Promise<void>}
 */
async function showVerifiedResult(result, hadError, type) {
  const ids = (result.itemIds || []).slice(0, 25);
  if (!result.ok || !ids.length) {
    setResult("err", "i-x", type === "save" ? "Not saved" : "PDF not attached", result.message || "", "");
    $("progress-title").textContent = "";
    return;
  }
  const statuses = (await Promise.all(ids.map((id) => api.itemStatus(id).catch(() => null)))).filter((s) => s && !s.in_trash);
  $("progress-title").textContent = "";
  if (!statuses.length) {
    setResult("err", "i-x", "Could not confirm the save", "ReviewHunter did not return the item. Open the app to check.", "");
    return;
  }
  const withPdf = statuses.filter((s) => s.has_pdf);
  const where = [...new Set(statuses.flatMap((s) => s.collections))];
  const place = where.length ? `In ${where.slice(0, 2).join(", ")}${where.length > 2 ? ` +${where.length - 2}` : ""}` : "In My Library";
  if (statuses.length === 1) {
    const s = statuses[0];
    const pdf = s.attachments.find((a) => a.mime === "application/pdf") || s.attachments[0];
    if (s.has_pdf) {
      setResult("ok", "i-check", "Saved with its PDF", s.title, `${place} · PDF ${formatSize(pdf ? pdf.size : 0)}`);
    } else if (result.watching) {
      setResult("warn", "i-minus", "Saved, PDF on its way", s.title, "Complete the check in the opened tab: the PDF will be attached and you will be notified.");
    } else {
      setResult("warn", "i-minus", "Saved without PDF", s.title, `${place} · ${hadError ? "the PDF could not be fetched" : "no PDF attached"}`);
    }
    return;
  }
  const all = withPdf.length === statuses.length;
  setResult(all ? "ok" : "warn", all ? "i-check" : "i-minus", `${statuses.length} items saved`,
    `${withPdf.length} with PDF, ${statuses.length - withPdf.length} without`, place);
}

/**
 * Fill the result banner.
 *
 * @param {"ok"|"warn"|"err"} tone  Colour.
 * @param {string} iconId  Symbol id.
 * @param {string} title   Headline.
 * @param {string} line1   First detail line.
 * @param {string} line2   Second detail line.
 * @returns {void}
 */
function setResult(tone, iconId, title, line1, line2) {
  $("result").className = `result ${tone}`;
  $("result-ico").setAttribute("href", `#${iconId}`);
  $("result-title").textContent = title;
  $("result-line1").textContent = line1;
  $("result-line1").title = line1;
  $("result-line2").textContent = line2;
  $("result-line2").hidden = !line2;
  $("result").hidden = false;
}

/**
 * Human readable byte size.
 *
 * @param {number} n  Bytes.
 * @returns {string} "1.2 MB" style, "" for 0.
 */
function formatSize(n) {
  if (!n) return "";
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Render the progress checklist.
 *
 * @param {Array<{id: string, label: string, state: string, detail?: string}>} steps  Steps.
 * @returns {void}
 */
function renderSteps(steps) {
  const ul = $("steps");
  ul.replaceChildren(...steps.map((s) => {
    const ic = s.state === "done" ? icon("i-check") : s.state === "error" ? icon("i-x") : s.state === "skipped" ? icon("i-minus") : s.state === "waiting" ? icon("i-alert") : "";
    return h("li", { className: s.state },
      h("span", { className: "st-icon" }, ic),
      h("div", {}, h("div", { className: "st-label", textContent: s.label }), s.detail ? h("div", { className: "st-detail", textContent: s.detail }) : null));
  }));
}

// ------------------------------------------------------------------------------------------
// Events
// ------------------------------------------------------------------------------------------

/**
 * Wire every static control once.
 *
 * @returns {void}
 */
function bindEvents() {
  $("btn-settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
  $("btn-offline-settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
  $("btn-retry").addEventListener("click", () => init({ force: true }));
  $("btn-save").addEventListener("click", startSave);
  $("btn-done").addEventListener("click", () => window.close());
  $("btn-open-app").addEventListener("click", async () => {
    await send({ type: "openApp", itemId: state.savedIds[0] });
    window.close();
  });

  const ta = $("single-title");
  ta.addEventListener("input", () => autosize(ta));

  $("multi-all").addEventListener("change", (e) => {
    const on = e.target.checked;
    state.selected = new Set(on ? state.detection.items.map((_, i) => i) : []);
    for (const cb of $("multi-list").querySelectorAll("input[type=checkbox]")) cb.checked = on;
    updateSelectionUi();
  });

  $("note-toggle").addEventListener("click", () => {
    const note = $("note");
    note.hidden = !note.hidden;
    $("note-toggle").textContent = note.hidden ? "+ Add a note" : "- Remove note";
    if (!note.hidden) note.focus();
  });

  // Collection picker.
  $("collection-btn").addEventListener("click", () => toggleCollectionPanel($("collection-panel").hidden));
  $("collection-search").addEventListener("input", renderCollectionList);
  $("collection-search").addEventListener("keydown", (e) => {
    const list = $("collection-list");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      moveActive(list, e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      const li = list.querySelector("li.active:not(.dd-empty)");
      if (li) chooseCollection(li.dataset.id === "" ? null : Number(li.dataset.id));
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      toggleCollectionPanel(false);
      $("collection-btn").focus();
    }
  });
  // Close the dropdown when clicking or tabbing anywhere else.
  const closeIfOutside = (e) => {
    if (!$("collection-panel").hidden && !$("collection-picker").contains(e.target)) toggleCollectionPanel(false);
  };
  document.addEventListener("mousedown", closeIfOutside);
  document.addEventListener("focusin", closeIfOutside);

  // Tags.
  const input = $("tag-input");
  $("tags-box").addEventListener("click", () => input.focus());
  input.addEventListener("input", () => {
    if (input.value.includes(",")) {
      input.value.split(",").slice(0, -1).forEach(addTag);
      input.value = input.value.split(",").pop();
    }
    renderTagSuggestions();
  });
  input.addEventListener("keydown", (e) => {
    const list = $("tag-suggest");
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !list.hidden) {
      e.preventDefault();
      moveActive(list, e.key === "ArrowDown" ? 1 : -1);
    } else if ((e.key === "Enter" || e.key === "Tab") && input.value.trim()) {
      e.preventDefault();
      e.stopPropagation();
      const active = !list.hidden && list.querySelector("li.active");
      addTag(active ? active.dataset.name : input.value);
      input.value = "";
      renderTagSuggestions();
    } else if (e.key === "Backspace" && !input.value && state.selectedTags.length) {
      state.selectedTags.pop();
      renderTags();
    } else if (e.key === "Escape" && !list.hidden) {
      e.preventDefault();
      e.stopPropagation();
      list.hidden = true;
    }
  });
  input.addEventListener("blur", () => setTimeout(() => ($("tag-suggest").hidden = true), 120));

  // Enter saves (Ctrl/Cmd+Enter inside multi-line fields).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.defaultPrevented || state.saving || $("footer").hidden) return;
    const t = e.target;
    const multiline = t && t.tagName === "TEXTAREA";
    if (multiline && !(e.ctrlKey || e.metaKey)) {
      if (t.id === "single-title") e.preventDefault(); // titles stay on one logical line
      else return;
    }
    if (t && t.tagName === "BUTTON" && t.id !== "btn-save") return;
    e.preventDefault();
    startSave();
  });
}

bindEvents();
init().catch((e) => {
  $("none-reason").textContent = `Unexpected error: ${e.message}`;
  showState("none");
});
