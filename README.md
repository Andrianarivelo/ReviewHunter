[![Version](https://img.shields.io/badge/version-1.1.1-blue)](https://github.com/Andrianarivelo/ReviewHunter)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Issues](https://img.shields.io/github/issues/Andrianarivelo/ReviewHunter)](https://github.com/Andrianarivelo/ReviewHunter/issues)
[![Size](https://img.shields.io/github/repo-size/Andrianarivelo/ReviewHunter.svg)](https://github.com/Andrianarivelo/ReviewHunter)
[![Downloads](https://img.shields.io/github/downloads/Andrianarivelo/ReviewHunter/total.svg)](https://github.com/Andrianarivelo/ReviewHunter/releases)

# ReviewHunter

**ReviewHunter** is a local bibliographic review app for collecting papers, resolving DOI metadata, finding real author publication lists, organizing literature into folders, and testing available PDF sources before saving files.

It ships with **ReviewHunter Web**, a Zotero-style local library app with folders, search, auto-categorization, auto-renaming, batch downloads, source-tested downloads, and a clean three-pane UI. One command launches it.

## Features

### CLI
- **Batch Download**: Download multiple research papers in one go using a list of DOIs or titles.
- **Title to DOI**: Retrieves DOIs using paper titles through the CrossRef API and downloads the corresponding papers.
- **Custom File Naming**: Automatically names files based on paper titles for easy identification.
- **Error Handling**: Logs failed downloads and provides detailed summaries after processing.

### Web app (`doi_hunter_web`)
- **Zotero-style three-pane UI** - collections, items, metadata + PDF preview.
- **Smart search bar** - paste a DOI, type a title, or filter the local library; CrossRef matches show inline.
- **Auto-rename** - files are saved as `Author - Year - Title.pdf` from CrossRef metadata.
- **Auto-categorize** - folder suggestions from CrossRef subjects + extracted keywords.
- **Folders & drag-drop** - create your own; drag papers from the list onto a folder to assign.
- **Batch import** - drop a `.txt` of DOIs/titles onto the window, or paste them in the Add dialog.
- **Live progress** - every download streams its status over WebSocket; toasts surface failures.
- **Local & private** - SQLite + your filesystem under `~/ReviewHunter/`. No login, no cloud.
- **Light & dark mode**, refined typography (Inter + Source Serif), zero build step.

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

Pass arguments to the app after `--`:
```bash
python run.py -- --port 9000 --no-browser
```

**Optional** - set `REVIEWHUNTER_MAILTO=you@example.com` to use the CrossRef polite pool (faster rate limits).

**Books & publications search** - ReviewHunter searches LibGen via two backends:

1. **[`libgen-api-modern`](https://github.com/leo283/libgen-api-modern)** *(primary)* - installed automatically by `run.py`. Searches Library Genesis (sci-tech and fiction) concurrently and returns direct download links, so books skip the mirror-page hop.
2. **FreeBooksAPI** *(fallback)* - used if `libgen-api-modern` fails or isn't installed. Override the host with `REVIEWHUNTER_FREEBOOKS_URL=https://your-host/api/latest`.

If both backends fail, the books panel hides itself and a one-line hint surfaces.

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

