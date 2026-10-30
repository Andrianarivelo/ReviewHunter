/**
 * Content script injected into the ReviewHunter web app (http://127.0.0.1:8765, http://localhost:8765).
 *
 * It is the bridge between the app page and the extension's service worker:
 *   page  -> extension   {source: "reviewhunter-app", type: "handoff", handoff}   a PDF the server
 *                         could not fetch; the extension tries with the browser session
 *   page  -> extension   {source: "reviewhunter-app", type: "ping"}               "are you there?"
 *   extension -> page    {source: "reviewhunter-connector", type: "hello", version, id}
 *   extension -> page    {source: "reviewhunter-connector", type: "handoff-ack", itemId, accepted, error}
 *
 * Only messages posted by the page itself (event.source === window) are relayed, and the service
 * worker checks that the sender tab is the configured ReviewHunter address before acting.
 * Plain script (content scripts are not ES modules).
 */
(() => {
  // Firefox and Safari: promise-based `browser.*`; Chrome and Edge: `chrome.*`.
  const chrome = globalThis.browser || globalThis.chrome;
  const CONNECTOR = "reviewhunter-connector";
  const APP = "reviewhunter-app";

  /** Tell the page which connector version is installed. */
  const announce = () => {
    let version = "";
    let id = "";
    try {
      version = chrome.runtime.getManifest().version;
      id = chrome.runtime.id; // lets ReviewHunter trust this connector to start it (native messaging)
    } catch (_) {
      /* extension reloaded: the page will ping again */
    }
    window.postMessage({ source: CONNECTOR, type: "hello", version, id }, location.origin);
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
      const ack = (accepted, error) =>
        window.postMessage({ source: CONNECTOR, type: "handoff-ack", itemId, accepted, error: error || "" }, location.origin);
      try {
        Promise.resolve(chrome.runtime.sendMessage({ type: "handoff", handoff: msg.handoff }))
          .then((res) => ack(!!(res && res.accepted), res && res.__rhError))
          .catch((e) => ack(false, (e && e.message) || String(e)));
      } catch (e) {
        ack(false, (e && e.message) || String(e));
      }
    }
  });

  announce();
})();
