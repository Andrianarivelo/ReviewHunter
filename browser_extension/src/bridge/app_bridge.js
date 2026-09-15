/**
 * Content script injected into the ReviewHunter web app (http://127.0.0.1:8765, http://localhost:8765).
 *
 * It is the bridge between the app page and the extension's service worker:
 *   page  -> extension   {source: "reviewhunter-app", type: "handoff", handoff}   a PDF the server
 *                         could not fetch; the extension tries with the browser session
 *   page  -> extension   {source: "reviewhunter-app", type: "ping"}               "are you there?"
 *   extension -> page    {source: "reviewhunter-connector", type: "hello", version}
 *   extension -> page    {source: "reviewhunter-connector", type: "handoff-ack", itemId, accepted, error}
 *
 * Only messages posted by the page itself (event.source === window) are relayed, and the service
 * worker checks that the sender tab is the configured ReviewHunter address before acting.
 * Plain script (content scripts are not ES modules).
 */
(() => {
  const CONNECTOR = "reviewhunter-connector";
  const APP = "reviewhunter-app";

  /** Tell the page which connector version is installed. */
  const announce = () => {
    let version = "";
    try {
      version = chrome.runtime.getManifest().version;
    } catch (_) {
      /* extension reloaded: the page will ping again */
    }
    window.postMessage({ source: CONNECTOR, type: "hello", version }, location.origin);
    // Also a DOM marker, so a page that initialises after this script still sees the connector.
    try {
      document.documentElement.setAttribute("data-reviewhunter-connector", version || "unknown");
    } catch (_) {
      /* no document yet */
    }
  };

  window.addEventListener("message", (event) => {
    if (event.source !== window || !event.data || event.data.source !== APP) return;
    const msg = event.data;
    if (msg.type === "ping") {
      announce();
      return;
    }
    if (msg.type === "handoff" && msg.handoff) {
      const itemId = msg.handoff.item_id;
      try {
        chrome.runtime.sendMessage({ type: "handoff", handoff: msg.handoff }, (res) => {
          const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : res && res.__rhError;
          window.postMessage({ source: CONNECTOR, type: "handoff-ack", itemId, accepted: !!(res && res.accepted), error: err || "" }, location.origin);
        });
      } catch (e) {
        window.postMessage({ source: CONNECTOR, type: "handoff-ack", itemId, accepted: false, error: (e && e.message) || String(e) }, location.origin);
      }
    }
  });

  announce();
})();
