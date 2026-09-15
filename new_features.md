# ReviewHunter insight maps — implementation prompt

Paste this whole file to a coding agent working in `D:\Apps\ReviewHunter` (venv `.venv`, Python 3.13,
FastAPI backend in `doi_hunter_web/`, vanilla Alpine.js front end in `doi_hunter_web/static/`). The app
runs on port 8765; use a dev instance on 8775 with `REVIEWHUNTER_HOME` under `%LOCALAPPDATA%\Temp\claude`
for testing so the user's real library at `C:\Users\andry\ReviewHunter` is untouched.

## Goal

Replace the single, weak "Concept Map" with a family of four **insight maps** that let a researcher see,
from a whole library **or a single collection / author / tag**, the global structure of a body of
literature: what it is about, what words dominate, what questions it asks, and — the hard one — **how
the papers actually did their science and what is missing**. Every map must be visually striking,
interactive, exportable, and honest (show the evidence sentence behind every claim; never invent data).

The four maps:

1. **Concept map** — themes and how they connect. Co-occurrence graph of curated tags/keywords plus
   phrases mined from titles and abstracts, grouped into colour-coded **clusters (communities)** so the
   reader sees themes, not a ring of dots. Force-directed layout, node size = number of papers, edge
   thickness = co-occurrence. Click a node → papers + neighbours; click a cluster → its sub-graph.
2. **Word map** — a real word cloud / bar view of the most frequent words and two-word phrases in
   abstracts (toggle: full text). When the scope is a subset of the library, show **distinctiveness**
   (log-odds vs the rest of the library) so the user sees what makes this collection special, not just
   generic words. Click a word → the papers that use it.
3. **Goal map** — what the papers set out to do. Extract each paper's **aim sentence(s)**, classify the
   **goal type** (test hypothesis / measure effect / mechanism / compare / develop method / review /
   describe), and pull **factor → outcome pairs** ("effect of X on Y", "whether X alters Y"). Render as
   a bipartite map (factors on the left, outcomes on the right, papers on the links) so the user sees
   which independent variables and which outcomes the field has and has not connected.
4. **Methodology map** — the flagship. For each paper, detect the methods it used across a **pipeline of
   stages**: Subjects & models → Study design → Manipulations → Measurements → Analyses. Show each
   paper as a horizontal pipeline of method chips in stage order, each chip carrying the **sentence that
   proves it** and a confidence (from the Methods section = high, from the abstract = author-stated,
   from the body ≥2 mentions = medium). Aggregate the scope into a **papers × methods matrix**
   (heatmap) and, above all, a **gaps panel**: reporting checks (sex of subjects, sample size,
   randomisation, blinding, ethics, power analysis, pre-registration, data/code availability, doses) and
   structural gaps (single model system, no human studies, a technique used by only one paper, a whole
   stage absent). This is what tells a researcher "what was done and what is missing".

## Already implemented (backend, working, tested against the real library)

These files exist and their `/api/insight/{kind}` endpoint returns correct JSON — do not rewrite them
from scratch, extend them:

- `doi_hunter_web/method_taxonomy.py` — the method taxonomy: ~60 methods across the 5 stages, each with
  word-bounded case-insensitive regexes, plus 11 reporting checks. Grow this list; it is the accuracy
  lever. Keep patterns conservative (a false positive is worse than a miss).
- `doi_hunter_web/insight.py` — `concepts()`, `words()`, `goals()`, `methods()`, all taking
  `(collection_id, author, tag)` scope. Reads title, abstract, tags, keywords/subjects and the indexed
  PDF full text (`fulltext_fts`), splits papers into sections on their headings (`split_sections`), and
  returns evidence sentences plus a `limits` block saying what could not be analysed (no abstract, no
  full text, no Methods section). Concept clustering is deterministic label propagation. Method
  detection requires ≥2 mentions in the body (so a rat paper citing a monkey study once is not tagged
  "Non-human primates") and singularises concept terms so "temperature"/"temperatures" merge and a
  unigram inside a kept phrase is dropped.
- `doi_hunter_web/server.py` — `GET /api/insight/{kind}?collection_id=&author=&tag=&source=&limit=&focus=`
  (kind ∈ concepts|words|goals|methods), runs the map in a worker thread.

Verified output on the user's 5-paper library: concepts cluster on "ambient temperature"; word map led
by temperature/mdma/ambient/rats; goal map found aim sentences and factor→outcome pairs for 4/5 papers;
methodology map read all 5 Methods sections and flagged real gaps (no pre-registration, no data
availability, blinding in 1/5, female subjects in 2/5).

## What remains to build

### A. Front end — the maps UI (the bulk of the work)

Replace the current concept-map view (`doi_hunter_web/static/js/discover.js` graph code + the
`virtual === 'graph'` block in `index.html` + graph CSS in `styles.css`). Design decisions:

- **New sidebar section "Insight maps"** with four entries (Concepts, Words, Goals, Methodology), each
  opening the corresponding map. Keep the existing "Concept Map" nav working (routes to Concepts).
- **Scope bar** at the top of every map: a dropdown that selects Whole library / a collection (with
  subcollections toggle) / the current author / a tag, plus the item count and a "reset" button.
  Opening a map while a collection is selected must default its scope to that collection — this is the
  explicit user request ("represent only one collection instead of the whole library").
- **Concept map**: force-directed graph (implement a small velocity-Verlet force sim in plain JS — no
  external lib. ~150 lines: repulsion between nodes, spring on edges, gravity to centre, run ~300 ticks
  on load then settle). Colour nodes by cluster, size by paper count, edge opacity by weight. Hover
  highlights a node's neighbourhood; click fills the right rail with the node's papers and top related
  terms; a "focus" mode re-queries with `focus=<term>`. A cluster legend lists each theme with its size.
  Must stay legible at 80 nodes and in both light and dark themes.
- **Word map**: a proper word-cloud layout (spiral placement, font size ∝ frequency or ∝ distinctiveness
  — a toggle) with a fallback ranked bar list. Source toggle Abstract/Full text. Click a word → papers.
- **Goal map**: two columns (factors, outcomes) with curved links; link thickness = number of papers;
  clicking a link lists the papers with their evidence sentence. Above it, a small bar of goal-type
  counts. A "gaps" line: outcomes studied by a single paper, papers with no detectable aim.
- **Methodology map**: primary view = one row per paper, method chips laid out along the 5 stages
  (subjects → analyses), chip colour by stage, tooltip = evidence sentence + confidence. Secondary view
  = papers × methods **heatmap** (methods sorted by prevalence). A prominent **Gaps & rigour panel**:
  the reporting-check bars (X of N papers) and the `insights.gaps` list. Every chip and cell links to
  the paper. This view is the product's differentiator; make it feel like a diagnostic dashboard.
- Follow the design language already in the app (serif headings, `card`, `action-secondary`, the dark
  palette). Reuse the existing right-rail pattern for node/paper detail.

### B. Export

Add a "Export" button on each map: PNG of the current view (serialise the SVG/canvas) and a CSV/JSON of
the underlying data (nodes+edges, words, goal links, or the methods matrix). The methods matrix CSV is
the one researchers will actually use in a review paper's supplementary.

### C. Backend refinements (accuracy)

- Grow `method_taxonomy.py` toward ~100 methods; add fields the user's domain needs (behavioural
  neuroscience, pharmacology) but keep it general.
- Optional semantic upgrade (only if `sentence-transformers` is installed — it is, see
  `embeddings.py`): cluster concept nodes and goal outcomes by embedding similarity instead of pure
  co-occurrence, and detect near-duplicate factors/outcomes ("meth self-administration" ≈
  "methamphetamine self-administration"). Gate it behind `embeddings.available()` and fall back to the
  rule-based path so the app still works CPU-only and offline.
- Goal-pair extraction is the weakest part: improve the regexes in `insight.py::_PAIR_RXS`, strip
  trailing method clauses, and merge factor/outcome synonyms. Add more aim cues.
- Methodology "order": right now `position` is the character offset of the first mention in the Methods
  section, used to order chips. Consider detecting explicit sequence cues ("first", "then", "subsequently",
  "after training", "on day N") to order the pipeline more faithfully.

### D. Tests and validation (mandatory, the repo's standard)

- `tests/test_insight.py` (pytest, isolated `library` fixture): build 3–4 items with known abstracts and
  a fake Methods-section full text (index it through `fulltext.index_item`), then assert: concept
  clusters and singular/plural merge; word counts and distinctiveness sign for a subset; goal type and a
  factor→outcome pair; method detection with the ≥2-in-body rule (a single "monkey" mention in a rat
  paper must NOT yield "Non-human primates"); reporting checks; and at least one gap string. Also assert
  collection scoping returns only that collection's items.
- Extend the front-end check: a headless snapshot via `tools/ui_snapshot.py` of each map on a dev
  instance, asserting no console errors and that nodes render.
- **Benchmark I** (`benchmarks/benchmark_methodology.py`): hand-label the methods of ~10 papers already
  in the library, run the detector, report precision/recall per stage with a small figure (thin bars,
  PDF+PNG+SVG in `benchmarks/results/`, as the other benchmarks do). This makes the accuracy claim
  honest and versionable.

### E. Docs and memory

- README: a short "Insight maps" subsection under the v2 feature table.
- Update `PROJECT_MEMORY.md` (gitignored) with the design and state.
- Commit as the user (identity already configured: Andrianarivelo / nnjiva@gmail.com), push to
  origin/main, no branches. Use the commit trailer the session requires.

## Constraints (repo conventions)

- No new runtime dependencies for the core path; `sentence-transformers`/`numpy` are optional and must
  be feature-gated. No Node build — vanilla JS only, no external CDN.
- Never mutate raw item data or fabricate results; every detected method, goal or concept must carry the
  sentence it came from. State limitations in the UI from the `limits` payloads.
- Parameters and styling constants at the top of each module/file, configurable.
- Analysis figures: elegant, compact, thin bars, no overlap, PDF+PNG+SVG.
- Test on a throwaway library, never the user's real one. Windows note: write helper scripts with the
  editor (Bash-tool heredocs mangle backslashes); keep scratch scripts under short paths.

## Acceptance

Opening any map for the whole library or for the "Temperature_addiction" collection shows a clear,
attractive visual within ~2 s; the methodology map names, per paper, the species/design/manipulations/
measurements/analyses with a verifiable sentence each, and a gaps panel a researcher would put in a
review's "limitations of the literature" paragraph. Tests green (`pytest tests -q`), harness pages pass,
Benchmark I figure committed.
