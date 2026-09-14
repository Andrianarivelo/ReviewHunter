# ReviewHunter Connector (Chrome extension)

Save references, and their PDFs, from any web page into your local ReviewHunter
library, like the Zotero Connector. Manifest V3, plain JavaScript ES modules, no
build step.

## Why it exists

Closed-access publishers (ScienceDirect, Wiley, IEEE, ...) refuse PDF downloads
from the ReviewHunter server (HTTP 403, bot protection) even when you have
institutional access. Your browser has the session cookies, so the extension
downloads the PDF **inside the browser** and uploads the bytes to ReviewHunter,
attaching them to the existing item (matched by DOI) or to a new one.

## Install (Chrome, Edge, Brave, Vivaldi)

1. Start ReviewHunter from its desktop shortcut (the connector talks to
   `http://127.0.0.1:8765`).
2. Open `chrome://extensions` (Edge: `edge://extensions`).
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** and select this folder: `D:\Apps\ReviewHunter\browser_extension`.
5. Pin the ReviewHunter button (puzzle icon, then the pin).
6. Optional: open the extension **Options** to change the server address, the
   default collection and the default toggles.

After pulling new code, press the reload arrow on the extension card in
`chrome://extensions`.

## Using it

| Where you are | What happens |
| --- | --- |
| Article page (ScienceDirect, Springer, Nature, Wiley, PubMed, PMC, arXiv, bioRxiv, IEEE, JSTOR...) | Popup shows the item, type, authors, DOI and whether it is already in the library. **Save** stores metadata, then downloads the PDF with your session and attaches it. |
| Search results (Google Scholar, PubMed, CrossRef) or any page listing 2+ DOIs | Checkbox list; selected entries are saved (identifier-only entries are resolved by the server). |
| A PDF tab | **Save PDF**. With a DOI / arXiv id in the URL, metadata is resolved and the PDF attached; otherwise the PDF is imported and ReviewHunter reads its metadata. If an earlier save could not get its PDF, the popup proposes **Attach** to that item. |
| Any other page | Saved as a web page (optional HTML snapshot, see Options). |

- Keyboard: **Ctrl+Shift+Y** (macOS: Cmd+Shift+Y) opens the popup, **Enter** saves.
  Change it at `chrome://extensions/shortcuts`.
- Right-click menus: *Save page*, *Save link* (DOI / arXiv / PubMed links are
  resolved, `.pdf` links are downloaded in the browser and imported),
  *Add selection as identifiers*.
- The badge shows how many references were detected on the tab; a grey icon
  means ReviewHunter is not running (checked at most every 20 s).

## How the PDF is obtained

For each candidate PDF URL (publisher endpoint, `citation_pdf_url`, links on the
page, open-access URL returned by the server), best first:

1. **page**: `fetch()` runs inside the article tab, so it is a same-origin
   request with your cookies and passes bot checks. Bytes are pulled out of the
   tab in 4 MB base64 chunks.
2. **extension**: `fetch()` from the service worker with `credentials: "include"`
   (host permissions bypass CORS).
3. **interstitial**: if 1 or 2 returned HTML (e.g. ScienceDirect's "redirecting
   to your PDF" page), the real link is read from its meta refresh, JavaScript
   redirect, viewer iframe or JSON, and 1 and 2 are retried once. Plain links
   inside the HTML are ignored so a login page linking to some help PDF is never
   attached.
4. **background tab** (last resort, can be disabled in Options): the URL is
   opened in an inactive tab, the extension waits for Chrome's PDF viewer, reads
   the bytes and closes the tab.

Bytes are uploaded only if they start with `%PDF-` and are at most 200 MB (the
server checks the same). If everything fails the popup says so, and the item is
remembered for 24 h: open the PDF yourself and click the button to attach it.

## Permissions, and why

| Permission | Reason |
| --- | --- |
| `activeTab`, `scripting` | Read citation metadata from the page you are on and run the in-page PDF download. |
| `tabs` | Badge per tab, PDF-tab detection, last-resort background tab, focusing the ReviewHunter window. |
| `storage` | Settings, last collection, pending PDF attachments (session only). |
| `contextMenus` | Right-click save actions. |
| `http://127.0.0.1/*`, `http://localhost/*` | Talk to the local ReviewHunter server. |
| `<all_urls>` | Download PDFs **with your browser session** from whichever publisher hosts them (this is what makes closed-access downloads work), and count references for the badge. |

Nothing is sent anywhere except your own ReviewHunter server. The server only
accepts state-changing requests from extension origins (or no Origin), so web
pages cannot write to your library.

## Files

```
manifest.json
icons/                 icon-{16,32,48,128}.png and grey variants (offline)
src/background.js      service worker: messages, save ports, badge, context menus
src/api.js             ReviewHunter connector API client
src/settings.js        settings, pending attachments, ping cache
src/core/detect.js     injects the extractor into a tab (dynamic import in the isolated world)
src/core/pdf.js        in-browser PDF acquisition strategies
src/core/save.js       save orchestration with a progress checklist
src/extract/           translator-lite: meta tags, JSON-LD, COinS, DOI discovery, site rules, multi-item pages
src/shared/identifiers.js  DOI / arXiv / PMID / PII helpers, interstitial parser, %PDF- check
src/popup/, src/options/, src/ui/common.css   user interface
tools/make_icons.py    regenerates the icons (Pillow)
test/                  browser harnesses, fixtures, API smoke test
```

## Tests

```bash
# Extractor + helpers (94 assertions) and PDF acquisition (11 assertions)
.venv/Scripts/python.exe browser_extension/test/serve.py 8799
#   open http://127.0.0.1:8799/test/test.html
#   open http://127.0.0.1:8799/test/pdf_test.html
#   open http://127.0.0.1:8799/test/popup_preview.html   (visual check of popup states, mocked chrome API)

# Connector API exactly as the extension calls it (use a throwaway library, not your real one)
REVIEWHUNTER_HOME=/tmp/rh-test REVIEWHUNTER_PORT=8785 .venv/Scripts/python.exe run.py -- --no-browser --port 8785 --no-https
.venv/Scripts/python.exe browser_extension/test/api_smoke.py http://127.0.0.1:8785
```

## Known limitations

- Sites that show a CAPTCHA or "verify you are human" page (Cloudflare, some
  JSTOR / Wiley flows) cannot be passed automatically. Solve it in a tab, open
  the PDF, then use **Save PDF / Attach**.
- If Chrome is set to *Download PDFs* instead of opening them
  (`chrome://settings/content/pdfDocuments`), the background-tab strategy cannot
  read the file.
- IEEE Xplore `stamp.jsp` pages wrap the PDF in an iframe; the extension targets
  `getPDF.jsp` directly, which works only with an active IEEE subscription.
- Cookie rules for requests sent by the service worker depend on Chrome's
  third-party cookie settings; the in-page strategy is not affected.
- *Save PDF* without DOI and *Save link* on `.pdf` links use the server's PDF
  import, which ignores tags and notes (the collection is applied).
- Site markup changes (Google Scholar, PubMed) can break the multi-item readers;
  generic metadata readers keep working.
