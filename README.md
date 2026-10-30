[![Version](https://img.shields.io/badge/version-2.7.0-blue)](https://github.com/Andrianarivelo/ReviewHunter)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Issues](https://img.shields.io/github/issues/Andrianarivelo/ReviewHunter)](https://github.com/Andrianarivelo/ReviewHunter/issues)
[![Size](https://img.shields.io/github/repo-size/Andrianarivelo/ReviewHunter.svg)](https://github.com/Andrianarivelo/ReviewHunter)
[![Downloads](https://img.shields.io/github/downloads/Andrianarivelo/ReviewHunter/total.svg)](https://github.com/Andrianarivelo/ReviewHunter/releases)

# ReviewHunter

**ReviewHunter** is a local, private reference manager built to replace Zotero for literature reviews:
collect papers and books, find legal PDFs, organise them in nested collections and tags, read and annotate
PDFs, write notes, and cite in Word or LaTeX with 10,000+ citation styles. Everything runs on your computer
(SQLite + your files); Google Drive is optional for storage and backups.

### Start and open ReviewHunter from Chrome; refreshed interface (2.10)

**The Chrome connector can start ReviewHunter.** Click the toolbar button while ReviewHunter is closed:
it starts in the background and the save form appears (a few seconds the first time). After saving,
**Open in ReviewHunter** shows that paper in the *same* browser window: an app tab already in the
window is reused (no reload), one from another window is moved here, otherwise a tab is opened.
The library button in the popup header, or right-click the toolbar button > **Open ReviewHunter
library**, opens the library the same way.

How: browsers do not let extensions start programs, except a helper registered by the program itself
(Chrome *native messaging*). ReviewHunter registers its helper for Chrome, Edge, Brave and Chromium (per
user, no administrator rights) when it is installed and at every launch, and only its own extension may
use it (it recognises the connector by its ID). After updating from an older connector, click
**Reload** on its card in `chrome://extensions`, and open ReviewHunter once from its shortcut so the
helper is registered.

**Refreshed interface.** New logo, smaller and calmer text, a lighter sidebar and list, a pill search
box, and an item pane that shows the fields that have a value (plus publication, date and DOI): the
others are one click away (*Show N empty fields*, remembered). A new library opens on a welcome page
with the three ways to start. Links of the form `http://127.0.0.1:8765/#item=<id>` open a paper.

### Zotero connector and shared libraries (2.8)

**Zotero connector** (Settings > Zotero). ReviewHunter finds Zotero by itself (for example
`D:\Program Files\Zotero`, version read from the installation) and the data folder Zotero really uses
(from Zotero's own preferences). It lists *My Library* and every *group library* separately (RSS feeds are
ignored), and you choose which ones to sync and where each one goes. A group lands in its own collection
by default, or in a shared library (see below) to share it with people who do not use Zotero.

* Re-syncing is incremental and field by field. Edits made in Zotero are applied, your ReviewHunter edits
  to other fields are kept, and tags you added here are never removed. When both sides changed the same
  field, you choose who wins (Zotero by default).
* Items you delete in Zotero go to the ReviewHunter trash, and come back if you restore them in Zotero.
* *Sync automatically* re-syncs when `zotero.sqlite` changes. An unchanged database costs well under 1 ms.
* Zotero can stay open. ReviewHunter only reads a verified copy of its database and never writes to Zotero
  (Zotero forbids other programs from modifying `zotero.sqlite`). The sync is therefore one-way.

**Shared libraries** (Settings > Shared Libraries, or right-click a collection > *Share via Google Drive...*).
A shared library is a collection that several people read and edit together through a Google Drive folder
(any synced folder works: OneDrive, Dropbox, a network share). Everyone can add papers, notes, tags and PDFs.
Drive's *Viewer* access gives a read-only member. To invite someone, share the folder in Drive. They add it
to *My Drive* (Shared with me > Organize > Add shortcut) and click *Find in Drive > Join* in ReviewHunter.

How it stays consistent without a server: each member only writes files of its own
(`records/<member>/<paper>.json`), so Drive never sees two people saving the same file. Every computer merges
everyone's files with a per-field last-writer-wins rule and a hybrid logical clock, so all members converge
to the same library. Two people editing *different* fields of a paper at the same time both keep their edit.
PDFs are stored once under their SHA-256 (`files/`). A PDF that Drive has not delivered yet is fetched on
a later sync. A deleted paper goes to everyone's trash, and restoring it anywhere restores it for everyone.

### Bulk publication import (2.7)

Open **Import** in the library toolbar, or right-click a collection and choose **Import
Publications Here**. Paste a publication list, select CSV/TSV or Word `.docx` lists,
choose several PDFs, or browse/paste a folder containing PDFs. BibTeX, RIS and
CSL-JSON imports remain available. Save legacy Word `.doc` documents as `.docx` first.

Choose the current collection, another collection, **My Library**, or **Create a new
collection**. The destination stays fixed while the import runs. PDFs are copied,
identified and attached to matching references; source files remain unchanged.
Citation metadata search and PDF search are optional. Uncertain matches are reported
for review instead of importing the first search result. Missing metadata stays missing.

The progress window reports each entry, including references saved without a PDF.
Close and reopen it while working, stop after the current entry, or save its report as
CSV. Jobs run while the app server stays open; reports expire after an hour or a server
restart. A batch supports up to 500 references, 200 MB per PDF, 10 MB per other file,
2 MB of pasted text and 1 GB combined. Folder imports can include subfolders.

### PDF imports and reference matching (2.2.1)

Save an open PDF with browser extension 1.2.2, drag a PDF into the library, or import a PDF
folder. ReviewHunter reads embedded Info/XMP metadata and the first pages, verifies available
registry metadata against the document, and attaches the file to its existing DOI or unique
matching title. If there is no matching reference, it creates one from the available metadata.
Unknown fields remain incomplete. Original PDF bytes are preserved.

Files are named `First Author et al - Year - Title.pdf`, with safe length limits. Re-importing
the same PDF reuses its reference and attachment. **Retrieve Metadata for PDF** also repairs
older standalone document entries: a verified match moves their files, notes and tags onto
the existing reference and puts the old entry in Trash. Different explicit DOIs are never
merged automatically.

After updating, restart ReviewHunter and reload its extension at `chrome://extensions`.
The extension first tries cached PDF bytes, then a fresh request. If an expired publisher link
still cannot be read, reopen it from the article page or use the viewer's Download button and
import that local PDF. The file is identified through the same matching pipeline.

Validation includes isolated Python tests, browser regression tests, real Chromium imports,
and [the PDF identity benchmark](benchmarks/results/H_pdf_identity_notes.md).

## What you get (v2)

| Area | Features |
|------|----------|
| Library | Zotero item types with type-aware fields, creators with roles, nested collections, colored tags + tag selector, starred, unfiled, trash with restore, related items, duplicate detection and merge |
| Adding items | Add by DOI / ISBN / PMID / arXiv, drag PDFs (metadata retrieved from the PDF), import BibTeX / BibLaTeX / RIS / CSL-JSON, **direct migration of a Zotero library** (collections, notes, files, annotations, saved searches), online discovery (CrossRef, author catalogues, books) |
| PDFs | Legal open-access finder (OpenAlex, Europe PMC) then publisher pages, built-in reader with highlights, underlines, sticky notes, find, thumbnails, outline, "note from annotations" |
| Notes & search | Rich-text child and standalone notes, full-text PDF index, search modes (title/creator/year, all fields and tags, everything), advanced search, saved searches |
| Citing | citeproc-js (the engine Zotero uses), 25 bundled styles + installer for any zotero.org style, Quick Copy (Ctrl+Shift+C / Ctrl+Shift+A), bibliography builder, reports, Better-BibTeX-style citation keys, export BibTeX / BibLaTeX / RIS / CSL-JSON / CSV / Word sources XML |
| Browser | **Chrome connector** (`browser_extension/`): saves the page you are reading and downloads its PDF inside your browser session, so papers you can access through your institution are captured too |
| Word | **Word add-in** (`word_addin/`): live citations and bibliography that renumber as you edit, footnotes for note styles, works offline from copies stored in the document |
| Storage | Google Drive for desktop folder mode or Google Drive API mode (`drive.file` scope), verified automatic backups with staged restore |
| Security | The local server rejects cross-site writes and DNS rebinding; only the app and browser extensions can modify the library |
| Impact factor | A free, OpenAlex-based journal impact metric next to the Publication column (sortable, filterable), with a per-journal search dialog and a manual override for a real Journal Impact Factor when you have one |

### Insight maps (v2.6)

Open **Concepts**, **Words**, **Goals**, or **Methodology** in the sidebar. Opening a map from a
collection inherits that collection; **Select collections** combines any checked collections, with
optional subcollections. Overlapping papers are counted once. The selection carries across all four
maps. The scope bar also supports authors, tags, and the whole library. Concepts combines readable
full-label cards and an overlap matrix with prevalence, shared-paper overlap and unusual concept
combinations. Broad indexing terms and single-paper concepts can be included explicitly. The sparse
network keeps two strong connections per concept; the matrix reconstructs every overlap from paper
memberships, including edges omitted by the network API's display cap. Words offers a cloud or ranked
bars with subset distinctiveness.

Goals opens a **Question atlas** with every scoped paper represented. Each visual card includes a
complete explicit aim, a clearly labelled title-derived focus, or an unavailable-text status. Cards
show supported factor/outcome relationships and preserve negative statements. Filter by question
type, theme, or search text; switch to **Factor–outcome connections** to compare relationships.
Click a card for its complete source evidence and all extracted relations. Both views paginate long
scopes; CSV and JSON retain every paper, including those without a reliable pair.
Aim search includes unheaded introductions and explicit retrospective purpose statements in Discussion
and Conclusion. [Aim-source audit](docs/aim-source-audit.md) documents recovery of both previously missed
aims on the same ten-paper sample, increasing explicit-aim coverage from eight to ten.

The **Outcomes** tab separates reported increases, decreases, no detected change, mixed responses and
unclear directions from research questions. Findings retain source sentences and experimental context.
Compatible-context groups show agreement and minority directions; a separate expandable section groups
shared outcomes where context differs or is incomplete. These are descriptive reading aids, not pooled
effects or statistical outlier tests. CSV exports the active view's records for every scoped paper;
JSON includes aims, findings and comparisons together. [Extraction limits](docs/result-extraction-audit.md)
and [visual checks](docs/insight-results/README.md) document the evidence and validation.

Words uses a stronger size range and stable colours for lexical concept groups. **Very rare** shows
terms occurring exactly once, with gold underlining. **Unusual in this scope** shows repeated terms
absent from available comparison text elsewhere in the library, with a disclosed log-odds threshold.
These are local text patterns, not claims of scientific novelty. Click a legend category to filter it,
or a term to inspect exact occurrence counts, paper counts and evidence. Rare candidates have their
own list, independent of the frequent-term cap. [Definitions and limits](docs/word-map-semantics.md).

Methodology arranges each paper across subjects/models, design, manipulations, measurements, and
analyses, with a prevalence-sorted heatmap and reporting/gaps dashboard. Click any chip, cell, term,
or goal link to inspect its source evidence and open its paper. Missing evidence means **not detected**,
not that a procedure was never performed. Confidence describes the source of the evidence, not a
probability that the claim is correct. Layouts and extraction run locally without a new runtime dependency.

Detailed chips now separate **Drugs & compounds**, **Recorded signals**, and **Techniques & tools**.
The vocabulary includes cocaine, amphetamine, methamphetamine and MDMA; spikes, LFP, calcium,
voltage and neurotransmitter signals; Neuropixels, tetrodes, patch-clamp, two-photon microscopy,
fiber photometry and specific optical sensors. Instruments do not automatically imply a measured
signal, and drug names require procedural context. [Taxonomy and sources](docs/method-detail-taxonomy.md).
ICSS now has its own assay chip. Specific design cues capture temperature-condition comparisons,
dose groups, randomised condition order and repeated treatment sessions. Random order does not
imply random allocation. [Source-text audit](docs/design-detail-audit.md) records the reported cases.

**Export** saves the displayed map as PNG or its data as CSV/JSON. The methodology CSV contains every
analysed paper and method (1 = detected, 0 = not detected); JSON retains evidence and reporting details.
Pipelines and heatmaps paginate long scopes; PNG exports the displayed page with reporting checks.
Scopes are capped at 2,000 papers, and goal extraction scans up to 60,000 indexed characters per paper.
Semantic embedding clustering is not enabled; the deterministic rule-based path works offline.

Validation: `pytest tests -q`; `tools/insight_fixture.py` creates an isolated synthetic library for
`tools/ui_snapshot.py --insights --fixture <fixture.json> --url http://127.0.0.1:8775 --out <image.png>`.
Benchmark I (`benchmarks/benchmark_methodology.py`) compares the initial and current detector on five
available real papers: precision 75.6% to 100%, recall 83.1% to 76.1%, for the fixed initial taxonomy.
These are agent-audited development annotations, pending independent human review, not general accuracy
estimates. The expanded 219-entry taxonomy includes additional details that are explicitly unscored.
CSV decisions, annotation evidence, and PDF/PNG/SVG figures make that tradeoff inspectable.

The [screenshot audit](docs/insight-audit/README.md) compares laptop and desktop layouts in both
themes, including evidence panels and an 80-node stress case. Version 2.3.1 improves label wrapping,
contrast, plot sizing and reporting denominators, and corrects negative-result and trial-design
detections. Reporting checks distinguish unavailable text from an assessed paper with no detection.

### First steps

1. Open **ReviewHunter** from its shortcut (Start menu or Desktop on Windows, Launchpad or Applications on macOS, the application menu on Linux). The app opens at `http://127.0.0.1:8765`; clicking again just brings it back.
2. **Coming from Zotero?** Settings > Zotero: tick the libraries to sync and click *Sync now* (or Import > *Migrate from Zotero* for a one-time copy). Zotero's files are only read.
3. **Chrome connector:** Settings > Browser Connector shows the folder to load in `chrome://extensions` (Developer mode > Load unpacked).
4. **Word add-in:** Settings > Word Add-in > *Set up Word add-in...* opens a script you run once (it trusts ReviewHunter's local certificate and registers the add-in), then restart Word: References tab > ReviewHunter.
5. **Google Drive:** Settings > Google Drive & Backup. Desktop mode needs only Google Drive for desktop; API mode walks you through creating a Desktop OAuth client.
6. **Working with colleagues:** right-click a top-level collection > *Share via Google Drive...*, then share the folder in Drive (Settings > Shared Libraries explains the three steps).

### How "Find PDF" works (v2.2)

The server tries, in order: legal open-access copies (OpenAlex, Unpaywall with your e-mail, Semantic Scholar,
Europe PMC, PubMed Central, bioRxiv / medRxiv, arXiv), the publisher page (`citation_pdf_url`, PDF links),
Sci-Hub mirrors (including Cloudflare-fronted proxies whose PDF host needs the article page as `Referer`) and
Anna's Archive SciDB. Requests carry a Chrome TLS fingerprint (`curl_cffi`) and trust the same certificates as
Chrome (built-in bundle + operating-system store), which is what gets past Cloudflare on Europe PMC and the
Sci-Hub proxies. Settings > PDF Sources holds the e-mail, an optional proxy (`socks5h://127.0.0.1:9150` for Tor
Browser, or an HTTP proxy), custom mirror lists, an EZproxy prefix and a **Test** button that lists, for any DOI,
every candidate and the exact reason each source gave nothing.

What the server still cannot fetch is **handed to the Chrome extension automatically**: JavaScript checks
(Cloudflare "Just a moment", DDoS-Guard, PubMed Central's cookie page), publisher PDFs that need your
institutional session, and Sci-Hub pages. The extension fetches them in a background tab with your own
session and attaches the PDF; a human check (ALTCHA, CAPTCHA) is never solved: the page opens for you and
the PDF is attached once the article appears. The item pane shows the progress and a **Retry in Chrome** button.

Failures are explained precisely, for example "blocked by the FortiClient web filter (UNIGE - Site bloque)"
(an organisation policy on the computer: no client-side trick gets past a block page, use another network or
ask IT), "domain no longer exists" (checked against public DNS-over-HTTPS) versus "blocked by your DNS
resolver", "mirror presents DDoS-Guard's self-signed certificate". Certificate verification is never disabled.

### Journal impact metrics (v2.3)

The **Impact Factor** column sits next to Publication in the item list (toggle it from the columns menu),
sortable and usable in Advanced Search (`Impact factor` field, "is at least" / "is at most"). The real Journal
Impact Factor is a paid Clarivate metric behind Journal Citation Reports and cannot legally be scraped, so what
is shown instead is OpenAlex's free, openly licensed **2-year mean citedness**: a correlated but
differently-scaled proxy, matched by ISSN (exact) or by journal name (similarity-matched, rejecting a weak
match rather than guessing). New items are looked up automatically in the background (Settings > General;
"Retrieve impact factors for the whole library" runs it on demand, one network call per distinct journal, so a
library with many papers in the same venue costs almost nothing extra).

Click the pill (list column, or the item pane's "Impact factor" row) to open **Journal impact metrics**: the
matched name, h-index and works indexed, a **search box** to fix a wrong auto-match against any other journal
name, and a **manual value** field for when your institution gives you the real JIF (Journal Citation Reports) -
it then takes priority everywhere and applies to every item in that journal at once.

### Tests and benchmarks

```bash
.venv/Scripts/python.exe -m pytest tests -q                     # backend, formats, API, Zotero migration
.venv/Scripts/python.exe tools/ui_snapshot.py --url http://127.0.0.1:8765/ --step "$(cat tests/e2e_ui.js)"   # UI scenario (test library only)
```
`benchmarks/benchmark_v1_v2.py` compares v1 and v2 (PDF availability, API latency, feature coverage); results and
figures are in `benchmarks/results/`. `benchmarks/benchmark_pdf_acquisition.py` (Benchmark G) measures how many
PDFs the server really downloads, previous resolver vs current, with paired statistics.
`benchmarks/benchmark_zotero_shared.py` (Benchmarks J and K) compares the previous one-shot Zotero importer with the
connector, and shared-library concurrency against single-file and per-paper alternatives (`measure` with the app
venv, `plot` with a Python that has matplotlib and SciPy; launcher `benchmarks/Run_Zotero_Shared_Benchmark.cmd`).
`tools/extension_e2e.py` runs the browser hand-off in a real headless Chrome with the extension loaded.

## Easy install (Windows, macOS, Linux)

Nothing to install first: no Python, no administrator password. If the computer has no suitable
Python, the installer gets a private one for your user account with [uv](https://docs.astral.sh/uv/).

| System | Do this | |
|---|---|---|
| **macOS** (Apple silicon and Intel) | open **Terminal** and paste:<br>`curl -fsSL https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/install.sh \| bash` | ReviewHunter then appears in **Launchpad**, **Spotlight** and **Applications** |
| **Windows 10/11** | download **Install-ReviewHunter.bat** from the [latest release](https://github.com/Andrianarivelo/ReviewHunter/releases/latest) and double-click it, or paste in PowerShell:<br>`irm https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/install.ps1 \| iex` | **Start menu** and **Desktop** shortcuts |
| **Linux** (Ubuntu, Debian, Fedora, openSUSE, Arch) | paste in a terminal:<br>`curl -fsSL https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/install.sh \| bash` | application menu and Desktop entries |

From the unzipped **ReviewHunter.zip** you can also double-click `Install-ReviewHunter.bat` (Windows) or
`Install ReviewHunter.command` (macOS; the first time, right-click it > **Open**, because macOS asks
about scripts downloaded from the internet), or run `bash install.sh` (Linux).

The setup window shows each step with a progress bar: system check, file copy, private Python environment,
dependencies, optional semantic clustering, a **self-test that really starts the app**, Chrome extension setup
and shortcuts. The program goes to `%LOCALAPPDATA%\Programs\ReviewHunter` (Windows), `~/Library/ReviewHunter`
(macOS) or `~/.local/share/reviewhunter` (Linux); your library in `~/ReviewHunter` is never touched, not even
by the uninstaller.

**The shortcut always opens the right ReviewHunter.** Every shortcut runs the same small launcher
(`python -m doi_hunter_web.launcher`), which
* opens the browser on the running ReviewHunter if it is this installation and this version (one instance only);
* replaces a running instance that comes from another folder or still runs older code (after an update);
* starts the server in the background (no console window; log in `~/ReviewHunter/logs/server.log`) and
  shows a dialog if it cannot start;
* checks the shortcuts at every launch and repairs any that point to another copy or an old launch method.

**Settings > Advanced > Application** shows the running version and folder, the state of each shortcut
(with *Repair shortcuts*), and **Restart** / **Quit** buttons.

**Chrome extension setup is selected by default.** The installer bundles and validates the current
connector. At the end it opens Chrome's extensions page and the extension folder (its path is copied to
the clipboard on Windows and macOS). Enable **Developer mode**, click **Load unpacked**, and select that
folder: Chrome requires this final manual step for extensions distributed outside the Chrome Web Store.
For an existing connector loaded from the same folder, click **Reload** instead.

```bash
bash install.sh --cli --yes --no-launch                            # unattended install, no app/browser launch
python installer/reviewhunter_installer.py --setup-chrome          # reopen Chrome setup for the installed app
python installer/reviewhunter_installer.py --uninstall             # remove program + shortcuts, keep library
python -m doi_hunter_web.launcher --foreground                     # run the server in a terminal (troubleshooting)
python -m doi_hunter_web.shortcuts --status                        # where the shortcuts point
```

Setup log: `%TEMP%\reviewhunter-install.log` or `$TMPDIR/reviewhunter-install.log`. Every installer CI run
packages `ReviewHunter.zip` from its exact commit and installs it on clean Windows, macOS (Apple silicon)
and Linux machines, with and without a preinstalled Python, then opens the app through its shortcut and
checks which instance answers.

## Launching the Web App - no install required

You only need Python 3.10+. From the project folder:

**Windows** - double-click `ReviewHunter.bat`, or:
```cmd
py run.py
```

**macOS / Linux**:
```bash
./run.sh
# or
python3 run.py
```

The first launch creates a local `.venv/` next to the project and installs dependencies into it (one-time, ~10s). Every subsequent launch just reuses that venv. No global `pip install`, no system pollution. Delete `.venv/` (or pass `--reinstall`) to start fresh.

ReviewHunter then starts on `http://127.0.0.1:8765` and your browser opens automatically.

The library lives at `~/ReviewHunter/` (override with `REVIEWHUNTER_HOME=...`). Renamed PDFs go in `~/ReviewHunter/papers/`; the catalog is `~/ReviewHunter/library.db` (SQLite).

Google Drive, backups, citation styles and integrations are configured in **Settings** (gear icon, Ctrl+,).

Pass arguments to the app after `--`:
```bash
python run.py -- --port 9000 --no-browser
```

**Optional** - set `REVIEWHUNTER_MAILTO=you@example.com` to use the CrossRef polite pool (faster rate limits).

**Books & publications search** - ReviewHunter searches metadata providers first,
then tries direct-download sources only when metadata search has no matches:

1. **Google Books** *(primary metadata)* - fast book discovery for titles, topics, authors, and ISBN-like queries.
2. **Open Library** *(metadata fallback)* - keeps book search available when Google Books has no response or no useful matches.
3. **[`libgen-api-modern`](https://github.com/leo283/libgen-api-modern)** *(download fallback)* - installed automatically by `run.py` and used for Library Genesis sci-tech/fiction results when metadata providers do not find anything.
4. **FreeBooksAPI** *(download fallback)* - used if LibGen search fails. Override the host with `REVIEWHUNTER_FREEBOOKS_URL=https://your-host/api/latest`.

Google Books and Open Library entries are metadata-only; they can be added to
the bibliography, but auto-download only runs when a result includes mirrors.

**Optional semantic clustering** - once running, `./.venv/bin/pip install sentence-transformers numpy` enables similarity-based grouping.

### Installing globally instead (optional)

If you'd rather have a system-wide command:
```bash
pip install "review-hunter[web]"
review-hunter-web
```

## CLI Setup & Usage

### Step 1: Create a Directory and Paper Title List

- Create a directory with any name.
- Inside that directory, create a .txt file  form example paper_titles.txt.
- This file should contain a list of paper titles or DOIs, each on a new line.

**Example content for paper_titles.txt:**

- AI Ethics: Balancing Innovation with Privacy and Security in the Digital Age
- 10.1002/er.6529
- Prediction of daily global solar radiation using different machine learning algorithms: Evaluation and comparison

### Step 2: Run the Downloader

Navigate to your created directory, then run the following command:

```bash
python -m doi_hunter paper_titles.txt --batch_size=5
````

## Output

Once the download starts, you'll see the following output:

```bash
[INFO] Welcome to ReviewHunter!
This tool helps you download research papers from Sci-Hub using DOIs or titles.
For more information and the source code, visit: https://github.com/Andrianarivelo/ReviewHunter

[INFO] Starting the download process...

Processing [1/3]: AI Ethics: Balancing Innovation with Privacy and Security in the Digital Age
[v] File downloaded successfully :)
Processing [2/3]: 10.1002/er.6529
[v] File already exists. Skipping download.

[SUMMARY]
Total papers in file: 2
Total successfully downloaded: 1
Total skipped files: 1
Total failed downloads: 0
````

**Summary of Logs**
At the end of the process, a summary is displayed showing the total number of papers processed, successfully downloaded, skipped (if they already exist), and failed downloads.

## Notes

The ```--batch_size``` option allows you to control how many papers are processed at once, useful for large datasets.
The doi of the research paper must start like this **10.1016/j.rser.2016.05.022**
Failed downloads are logged and can be retried later.

## Contributors

- [@Faisal-PCB](https://github.com/Faisal-PCB)
