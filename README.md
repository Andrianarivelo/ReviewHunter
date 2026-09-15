[![Version](https://img.shields.io/badge/version-2.0.0-blue)](https://github.com/Andrianarivelo/ReviewHunter)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Issues](https://img.shields.io/github/issues/Andrianarivelo/ReviewHunter)](https://github.com/Andrianarivelo/ReviewHunter/issues)
[![Size](https://img.shields.io/github/repo-size/Andrianarivelo/ReviewHunter.svg)](https://github.com/Andrianarivelo/ReviewHunter)
[![Downloads](https://img.shields.io/github/downloads/Andrianarivelo/ReviewHunter/total.svg)](https://github.com/Andrianarivelo/ReviewHunter/releases)

# ReviewHunter

**ReviewHunter** is a local, private reference manager built to replace Zotero for literature reviews:
collect papers and books, find legal PDFs, organise them in nested collections and tags, read and annotate
PDFs, write notes, and cite in Word or LaTeX with 10,000+ citation styles. Everything runs on your computer
(SQLite + your files); Google Drive is optional for storage and backups.

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

### Insight maps (v2.3)

Open **Concepts**, **Words**, **Goals**, or **Methodology** in the sidebar. Opening a map from a
collection inherits that collection; the scope bar also supports subcollections, authors, tags, and the
whole library. Concepts show clustered co-occurrence, Words offers a cloud or ranked bars with
subset distinctiveness, and Goals connects factors to outcomes with the authors' aim sentences.

Methodology arranges each paper across subjects/models, design, manipulations, measurements, and
analyses, with a prevalence-sorted heatmap and reporting/gaps dashboard. Click any chip, cell, term,
or goal link to inspect its source evidence and open its paper. Missing evidence means **not detected**,
not that a procedure was never performed. Confidence describes the source of the evidence, not a
probability that the claim is correct. Layouts and extraction run locally without a new runtime dependency.

**Export** saves the displayed map as PNG or its data as CSV/JSON. The methodology CSV contains every
analysed paper and method (1 = detected, 0 = not detected); JSON retains evidence and reporting details.
Pipelines and heatmaps paginate long scopes; PNG exports the displayed page with reporting checks.
Scopes are capped at 2,000 papers, and goal extraction scans up to 60,000 indexed characters per paper.
Semantic embedding clustering is not enabled; the deterministic rule-based path works offline.

Validation: `pytest tests -q`; `tools/insight_fixture.py` creates an isolated synthetic library for
`tools/ui_snapshot.py --insights --fixture <fixture.json> --url http://127.0.0.1:8775 --out <image.png>`.
Benchmark I (`benchmarks/benchmark_methodology.py`) compares the initial and current detector on five
available real papers: precision 75.6% to 100%, recall 83.1% to 74.6%, for the fixed initial taxonomy.
These are agent-audited development annotations, pending independent human review, not general accuracy
estimates. The expanded 100-method taxonomy includes additional methods that are explicitly unscored.
CSV decisions, annotation evidence, and PDF/PNG/SVG figures make that tradeoff inspectable.

The [screenshot audit](docs/insight-audit/README.md) compares laptop and desktop layouts in both
themes, including evidence panels and an 80-node stress case. Version 2.3.1 improves label wrapping,
contrast, plot sizing and reporting denominators, and corrects negative-result and trial-design
detections. Reporting checks distinguish unavailable text from an assessed paper with no detection.

### First steps

1. Double-click the **ReviewHunter** shortcut (or `ReviewHunter.bat`). The app opens at `http://127.0.0.1:8765`.
2. **Coming from Zotero?** Import (toolbar) > *Migrate from Zotero* > Detect > Import. Zotero's files are only read.
3. **Chrome connector:** Settings > Browser Connector shows the folder to load in `chrome://extensions` (Developer mode > Load unpacked).
4. **Word add-in:** Settings > Word Add-in > *Set up Word add-in...* opens a script you run once (it trusts ReviewHunter's local certificate and registers the add-in), then restart Word: References tab > ReviewHunter.
5. **Google Drive:** Settings > Google Drive & Backup. Desktop mode needs only Google Drive for desktop; API mode walks you through creating a Desktop OAuth client.

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

### Tests and benchmarks

```bash
.venv/Scripts/python.exe -m pytest tests -q                     # backend, formats, API, Zotero migration
.venv/Scripts/python.exe tools/ui_snapshot.py --url http://127.0.0.1:8765/ --step "$(cat tests/e2e_ui.js)"   # UI scenario (test library only)
```
`benchmarks/benchmark_v1_v2.py` compares v1 and v2 (PDF availability, API latency, feature coverage); results and
figures are in `benchmarks/results/`. `benchmarks/benchmark_pdf_acquisition.py` (Benchmark G) measures how many
PDFs the server really downloads, previous resolver vs current, with paired statistics.
`tools/extension_e2e.py` runs the browser hand-off in a real headless Chrome with the extension loaded.

## Easy install (Windows and Linux)

Download **ReviewHunter.zip** from the [latest release](https://github.com/Andrianarivelo/ReviewHunter/releases/latest), unzip it, then:

| System | One click | What happens |
|---|---|---|
| **Windows 10/11** | double-click `Install-ReviewHunter.bat` | finds Python 3.10+ (offers to install Python 3.12 with winget if missing), then opens the setup window |
| **Linux** (Ubuntu, Debian, Fedora, openSUSE, Arch) | `bash install.sh` | installs `python3-venv` / `python3-tk` with your confirmation if missing, then opens the setup window (terminal mode without a display) |

The setup window shows each step with a progress bar: system check, file copy, private Python environment,
dependencies, optional semantic clustering, a **self-test that really starts the app**, Chrome extension setup, shortcuts
(Desktop + Start menu / applications menu). Nothing needs administrator rights: the program goes to
`%LOCALAPPDATA%\Programs\ReviewHunter` (Windows) or `~/.local/share/reviewhunter` (Linux), and your library
in `~/ReviewHunter` is never touched, not even by the uninstaller.

**Chrome extension setup is selected by default.** The installer bundles and validates the current
connector alongside the PDF metadata, reference matching, and citation filename features. At the end it
opens Chrome's extensions page and the installed extension folder (the folder path is copied on Windows).
Enable **Developer mode**, click **Load unpacked**, and select that folder. Chrome requires this final
manual step for extensions distributed outside the Chrome Web Store. For an existing connector loaded
from the same folder, click **Reload** instead. On Windows, **ReviewHunter Chrome Extension** in the Start
menu, or **Install Chrome Extension.cmd** in the installation folder, reopens setup later.

```bash
bash install.sh --cli --yes --no-launch                            # unattended install, no app/browser launch
python installer/reviewhunter_installer.py --setup-chrome          # reopen Chrome setup for the installed app
python installer/reviewhunter_installer.py --no-chrome-extension   # skip Chrome setup
python installer/reviewhunter_installer.py --uninstall             # remove program + shortcuts, keep library
python installer/reviewhunter_installer.py --help                  # all options
```

`Install-ReviewHunter.bat` and `install.sh` also work on their own (downloaded without the zip): they fetch the
latest release. While the repository is private this needs the [GitHub CLI](https://cli.github.com/) signed in
(`gh auth login`); otherwise download the zip from the Releases page while signed in to GitHub.
Setup log: `%TEMP%\reviewhunter-install.log` or `/tmp/reviewhunter-install.log`.

Every installer CI run packages `ReviewHunter.zip` and `ReviewHunter-Chrome-Extension.zip` from its exact
commit. The **ReviewHunter-installer** workflow artifact contains both ZIPs and the Windows/Linux
launchers. Automated clean installations use this same packaged app and verify its PDF dependencies
and connector files before reporting success.

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

