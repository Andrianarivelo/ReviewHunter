# ReviewHunter for Microsoft Word

Live citations and bibliographies from your local ReviewHunter library, in the spirit of
the Zotero Word plugin. Plain browser JavaScript (no build step), served by ReviewHunter at
`https://localhost:<https_port>/addin/` (default port 8766).

## Install (Windows, Microsoft 365 / Word 2016+)

**One click:** Start menu > ReviewHunter Add-ons > **ReviewHunter for Word** (or Settings > Word
Add-in > *Add to Word*). No console: Windows shows its own warning about installing the
"ReviewHunter Local CA" certificate (click **Yes**), then a dialog says what to do next. Restart
Word, open a document, click **References > ReviewHunter** (or **Home > Add-ins > Developer
Add-ins > ReviewHunter**).

What it does (`scripts\setup_word_addin.ps1 -Quiet`; *Detailed setup...* runs the same script in a
console that explains each step):
* trusts "ReviewHunter Local CA" for your Windows account only (no administrator rights),
* registers the manifest under `HKCU\Software\Microsoft\Office\16.0\WEF\Developer`,
* clears the Office add-in cache when Word is closed (it only holds copies of add-in files).

The CA certificate carries critical X.509 **name constraints**: it can only vouch for `localhost`,
`127.0.0.0/8` and `::1`, so even a leaked key could not impersonate a real website. A CA made by a
version before 2.11 (without constraints) is replaced automatically; run *ReviewHunter for Word*
once more to trust the new one. `tests/test_addin_ca.py` proves it with real TLS handshakes.

Remove everything with `scripts\setup_word_addin.cmd -Uninstall`.

## Install (macOS, Word for Mac 16+)

**ReviewHunter for Word** in `~/Applications/ReviewHunter Add-ons` trusts the CA in your login keychain
(macOS asks for your password) and copies the manifest to
`~/Library/Containers/com.microsoft.Word/Data/Documents/wef`, where Word for Mac reads sideloaded
add-ins. Restart Word: **Home > Add-ins > ReviewHunter**. Not yet tried on a Mac with Word: please
report what you see.

## Using it

| Action | How |
| --- | --- |
| Cite | Type in the search box, `Enter` adds the highlighted reference to the citation, `Ctrl+Enter` inserts. Several references make one citation. |
| Page, prefix, suffix | Click a reference in the citation (or its slider icon): locator type + value, prefix, suffix, suppress author. Drag the handle or use the arrows to reorder. |
| Preview | Rendered live in the document style, numbered as it would be at the end of the document. |
| Edit / remove | Put the cursor in a citation: the pane loads it ("Update citation", "Remove"). |
| Bibliography | Inserts one bibliography at the cursor; later clicks update it. |
| Refresh | Re-renders every citation in document order (numbering, disambiguation, ibid.) and the bibliography. Runs automatically after every change. |
| Document | Style (grouped by author-date, numeric, note), language, field look, and **Unlink citations** (plain text for submission, asks you to type UNLINK). |

Note styles (for example Chicago notes) put each citation in a real footnote when Word
supports WordApi 1.5; switching between note and in-text styles moves citations into or
out of footnotes. Older Word builds keep them in the text and show a warning.

## How it works

```
taskpane.html / taskpane.js   UI only (search, citation builder, preferences)
js/rh-core.js                 all citation logic, adapter independent (RH.Controller)
js/rh-word-adapter.js         Office.js implementation (content controls, footnotes, settings)
js/rh-mock-adapter.js         contenteditable "document" for browser development and tests
/static/js/citations.js       citeproc-js wrapper shared with the web app
```

**In the document**

* Citation: rich-text content control, tag `RH-CITE:<citationID>`, title "ReviewHunter citation".
* Bibliography: one rich-text content control, tag `RH-BIBL`.
* Data in Office document settings (fallback: CustomXMLPart `urn:reviewhunter:citations`):
  * `rh.citations`: `{ citationID: { items: [{id, locator, label, prefix, suffix, suppress-author}] } }`
  * `rh.items`: CSL-JSON snapshot of every cited item, so the document renders without the server
  * `rh.prefs`: `{ style, locale, appearance }`
  * `rh.trash`: the last 25 clusters whose control disappeared, so Undo or cut/paste restores them

**Refresh**: list `RH-CITE` controls in document order (body and footnotes merged),
give pasted duplicates a new id, restore clusters from the trash, move orphans to the trash,
update snapshots from `/api/csl/items` when online, run `RHCitations.processDocument`,
write each control with `insertHtml(..., "Replace")` and the bibliography, save settings.

**Offline**: the style and locale XML last used are cached in the pane's localStorage;
citation data comes from the snapshots in the document. Search needs the app running.

## Development and tests

* Dev mode: open `http://127.0.0.1:<port>/addin/taskpane.html?mock=1` in a browser. Without
  Word the pane drives a simulated document shown next to it; `window.rhApp` exposes the
  controller and adapter.
* Automated scenarios: `http://127.0.0.1:<port>/addin/test/test.html` (needs a library
  containing Schultz 1997, Hopfield 1982, Hodgkin and Huxley 1952, Vaswani 2017). Prints
  PASS/FAIL counts; `window.RH_TEST_RESULT` holds them.
* Visual demo for screenshots: `test/demo.html?style=nature&theme=dark`, for example
  `chrome --headless=new --window-size=1100,760 --virtual-time-budget=30000 --screenshot=out.png <url>`.
* Icons: `.venv/Scripts/python.exe word_addin/tools/make_icons.py`.

## Limitations

* Tested against the mock document only; the Word adapter follows the documented Word API
  but has not been exercised inside Word by the test suite.
* Office.js is loaded from Microsoft's CDN, so Word needs internet access to open the pane.
* Word for Mac and Word on the web are not covered by the setup script (registry sideloading
  is Windows only) and have not been tested.
* A document opened on a computer without ReviewHunter keeps its formatted citations as
  normal text inside content controls; it simply cannot be refreshed there.
* Word's HTML import decides the final look of bibliography paragraphs; hanging indents and
  spacing are set with inline CSS and may differ slightly from Zotero's output.
