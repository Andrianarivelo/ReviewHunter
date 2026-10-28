"""Benchmarks J (Zotero connector) and K (shared-library concurrency).

J - Zotero: previous version (one-shot importer, git revision PREV_REV, run from a worktree) versus the
    smart connector, on identical synthetic Zotero 10 databases (My Library + a group + an RSS feed).
    Per trial: initial import, then ZOTERO_EDITS edits made in Zotero and LOCAL_EDITS edits made in
    ReviewHunter, then the same entry point is called again (``zotero_import.import_library``).
    Measured: fraction of Zotero edits reflected locally (per edit type), fraction of local edits kept,
    feed items leaked into the library, wall time of initial import / re-import / idle re-check.

K - Shared libraries: our per-writer, per-field merge (the REAL engine, end to end, M ReviewHunter
    libraries on one folder) versus two common alternatives simulated on the very same edit script and
    sync order: one library file in Drive (whole-file last-writer-wins) and one file per paper
    (whole-record last-writer-wins). Members edit concurrently (offline), then sync in random order.
    Measured: lost non-conflicting edits (an edit nobody else touched that is missing at the end),
    convergence (all members identical), same-field conflicts.

Usage (repo root):
    .venv/Scripts/python.exe benchmarks/benchmark_zotero_shared.py measure      # app venv
    %USERPROFILE%/miniconda3/python.exe benchmarks/benchmark_zotero_shared.py plot   # matplotlib + scipy
Outputs: benchmarks/results/J_zotero_sync.{png,pdf,svg}, K_shared_concurrency.{png,pdf,svg} + CSVs.
"""
from __future__ import annotations

# ======================================================================================
# Parameters (edit here)
# ======================================================================================
PREV_REV = "5c877b5"                  # previous release (before the smart connector)
PREV_WORKTREE = r"C:\Users\andry\AppData\Local\Temp\claude\rh-prev"
J_TRIALS = 20                         # paired trials (seeds) per version
J_USER_ITEMS = 300                    # My Library items
J_GROUP_ITEMS = 100
J_FEED_ITEMS = 50
J_EDITS = {"title": 10, "tag_add": 5, "tag_remove": 5, "trash": 5, "new_item": 10}
J_LOCAL_EDITS = 20
K_TRIALS = 30                         # trials per team size
K_TEAM_SIZES = (2, 3, 4)
K_PAPERS = 30
K_EDITS_PER_MEMBER = 6
K_FIELDS = ("title", "abstractNote", "volume", "pages", "extra")
K_TAG_PROB = 0.3                      # probability that an edit is a tag addition instead of a field edit
SEED = 20261028
# Figure styling
FIG_DPI = 300
FONT_SIZE = 8
BAR_WIDTH = 0.22                      # thin bars
COLORS = {"previous": "#9aa3b2", "connector": "#0b84c6", "whole_file": "#c9a227",
          "per_record": "#d9822b", "per_field": "#0b84c6"}
ALPHA = 0.05

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "benchmarks" / "results"
RAW_J = RESULTS / "J_zotero_sync_trials.csv"
RAW_K = RESULTS / "K_shared_concurrency_trials.csv"


# ======================================================================================
# J worker (runs inside a subprocess whose sys.path points at one code version)
# ======================================================================================

def _j_worker(code_root: str, seed: int, workdir: str) -> dict:
    """One trial for one code version. Builds the same Zotero library for a given seed."""
    sys.path[:0] = [code_root, str(REPO / "tests")]
    os.environ["REVIEWHUNTER_HOME"] = str(Path(workdir) / "lib")
    from test_zotero_sync import FakeZotero  # synthetic Zotero 10 schema (no app code)

    from doi_hunter_web import catalog, zotero_import
    from doi_hunter_web.db import get_conn

    rng = random.Random(seed)
    z = FakeZotero(Path(workdir))
    z.sql("INSERT INTO collections VALUES " + ",".join(f"({i},'User col {i}',NULL,NULL,1,'COLU{i:04d}')" for i in range(1, 6))
          + ",(6,'Group A',NULL,NULL,2,'COLG0001'),(7,'Group B',6,NULL,2,'COLG0002')")
    vocab = [f"kw{i}" for i in range(15)]
    item_id = 0
    user_ids = []
    for i in range(J_USER_ITEMS):
        item_id += 1
        fields = {1: f"User paper {seed}-{i}", 3: str(1990 + rng.randint(0, 35))}
        if rng.random() < 0.7:
            fields[4] = f"10.5555/u.{seed}.{i}"
        z.add_item(item_id, f"U{item_id:07d}", fields=fields, creators=[("Ana", f"Author{i % 40}")],
                   tags=rng.sample(vocab, rng.randint(0, 3)), collections=[rng.randint(1, 5)])
        user_ids.append(item_id)
    for i in range(J_GROUP_ITEMS):
        item_id += 1
        z.add_item(item_id, f"G{item_id:07d}", lib=2, fields={1: f"Group paper {seed}-{i}"}, collections=[rng.choice([6, 7])])
    for i in range(J_FEED_ITEMS):
        item_id += 1
        z.add_item(item_id, f"F{item_id:07d}", lib=3, fields={1: f"FEED item {seed}-{i}"})

    # Papers whose tag will be removed in Zotero must carry that tag BEFORE the first import, otherwise
    # "tag removed" would be trivially true for an importer that never imported the tag.
    remove_targets = rng.sample(user_ids, J_EDITS["tag_remove"])
    for zid in remove_targets:
        z.add_tag(zid, "to-remove")

    t0 = time.perf_counter()
    zotero_import.import_library(z.dir)
    t_initial = time.perf_counter() - t0

    def local_by_title(title):
        row = get_conn().execute("SELECT id FROM items WHERE title=?", (title,)).fetchone()
        return row[0] if row else None

    # Local edits in ReviewHunter (abstract of random papers).
    local_targets = rng.sample([x for x in user_ids if x not in remove_targets], J_LOCAL_EDITS)
    for zid in local_targets:
        iid = local_by_title(f"User paper {seed}-{zid - 1}")
        catalog.patch_item(iid, {"field_values": {"abstractNote": f"LOCAL EDIT {zid}"}})

    # Edits in Zotero, on papers not edited locally.
    pool = [x for x in user_ids if x not in local_targets and x not in remove_targets]
    rng.shuffle(pool)
    expected = []
    for kind, n in J_EDITS.items():
        for _ in range(n):
            if kind == "new_item":
                item_id += 1
                z.add_item(item_id, f"N{item_id:07d}", fields={1: f"New paper {seed}-{item_id}"})
                expected.append((kind, f"New paper {seed}-{item_id}", None))
                continue
            zid = pool.pop()
            title = f"User paper {seed}-{zid - 1}"
            if kind == "title":
                z.set_field(zid, 1, title + " (revised)")
                expected.append((kind, title + " (revised)", None))
            elif kind == "tag_add":
                z.add_tag(zid, "added-in-zotero")
                expected.append((kind, title, "added-in-zotero"))
            elif kind == "tag_remove":
                pool.append(zid)                  # not used: removal targets were tagged before the import
                zid = remove_targets[sum(1 for e in expected if e[0] == "tag_remove")]
                z.sql("DELETE FROM itemTags WHERE itemID=? AND tagID=(SELECT tagID FROM tags WHERE name='to-remove')",
                      (zid,), touch=zid)
                expected.append((kind, f"User paper {seed}-{zid - 1}", "to-remove"))
            elif kind == "trash":
                z.sql("INSERT INTO deletedItems (itemID) VALUES (?)", (zid,), touch=zid)
                expected.append((kind, title, None))
    t0 = time.perf_counter()
    zotero_import.import_library(z.dir)
    t_resync = time.perf_counter() - t0
    t_idle = float("nan")
    try:
        from doi_hunter_web import zotero_sync  # only exists in the new version
        zotero_sync.sync(z.dir, libraries=["user", "group:98765"])          # sets the fingerprint
        t0 = time.perf_counter()
        zotero_sync.sync(z.dir, libraries=["user", "group:98765"], force=False)
        t_idle = time.perf_counter() - t0
    except ImportError:
        pass

    conn = get_conn()
    hits = {k: 0 for k in J_EDITS}
    for kind, title, tag in expected:
        iid = local_by_title(title)
        if kind in ("title", "new_item"):
            ok = iid is not None
        elif kind == "trash":
            ok = bool(iid and conn.execute("SELECT deleted_at FROM items WHERE id=?", (iid,)).fetchone()[0])
        else:
            has = bool(iid and conn.execute("SELECT 1 FROM item_tags it JOIN tags t ON t.id=it.tag_id WHERE it.item_id=? "
                                             "AND t.name=?", (iid, tag)).fetchone())
            ok = has if kind == "tag_add" else not has
        hits[kind] += ok
    kept = sum(1 for zid in local_targets
               if conn.execute("SELECT abstract FROM items WHERE title=?", (f"User paper {seed}-{zid - 1}",)).fetchone()[0]
               == f"LOCAL EDIT {zid}")
    leaked = conn.execute("SELECT COUNT(*) FROM items WHERE title LIKE 'FEED item%'").fetchone()[0]
    out = {"seed": seed, "t_initial": t_initial, "t_resync": t_resync, "t_idle": t_idle,
           "local_kept": kept / J_LOCAL_EDITS, "feed_leaked": leaked,
           "zotero_edits_reflected": sum(hits.values()) / sum(J_EDITS.values())}
    out.update({f"reflected_{k}": hits[k] / J_EDITS[k] for k in J_EDITS})
    return out


# ======================================================================================
# K: concurrent editing, real engine + simulated alternatives
# ======================================================================================

def _k_trial(members: int, seed: int, workdir: Path) -> list[dict]:
    from doi_hunter_web import catalog, config, db, shared_library as sl

    rng = random.Random(seed)
    drive = workdir / "Drive"
    drive.mkdir(parents=True)

    def as_(m):
        os.environ["REVIEWHUNTER_HOME"] = str(workdir / f"m{m}")
        db.reset_connections()

    def by_doi(n):
        row = db.get_conn().execute("SELECT id FROM items WHERE doi=?", (f"10.9999/p{n}",)).fetchone()
        return row[0]

    # Setup: member 0 shares P papers, the others join.
    as_(0)
    cid = db.get_conn().execute("INSERT INTO collections (name) VALUES ('Lab')").lastrowid
    v0 = {}
    for n in range(K_PAPERS):
        catalog.create_item({"title": f"Paper {n}", "doi": f"10.9999/p{n}", "collections": [cid]})
        v0[n] = {"title": f"Paper {n}"}
    sl.create("Lab", str(drive), collection_id=cid)
    lib_id = sl.list_libraries()[0]["library_id"]
    for m in range(1, members):
        as_(m)
        sl.join(str(drive / "Lab"))

    # Concurrent (offline) edits: every member edits before anybody syncs.
    edits = []   # (member, paper, key, value)
    for m in range(members):
        as_(m)
        for j in range(K_EDITS_PER_MEMBER):
            n = rng.randrange(K_PAPERS)
            iid = by_doi(n)
            if rng.random() < K_TAG_PROB:
                key, value = f"tag:t{m}-{j}", True
                catalog.add_tag(iid, f"t{m}-{j}")
            else:
                field = rng.choice(K_FIELDS)
                key, value = field, f"m{m}-e{j}"
                catalog.patch_item(iid, {"field_values": {field: value}})
            edits.append((m, n, key, value))
    order = list(range(members))
    rng.shuffle(order)
    for m in order + order:                       # each member syncs twice, Drive delivery order random
        as_(m)
        sl.sync(lib_id)

    # Final states of the real engine.
    finals = []
    for m in range(members):
        as_(m)
        state = {}
        for n in range(K_PAPERS):
            item = catalog.get_item(by_doi(n))
            fv = item["field_values"]
            state[n] = {f: fv.get(f) or None for f in K_FIELDS}
            state[n].update({f"tag:{t['name']}": True for t in item["tags"]})
        finals.append(state)

    # Ground truth: (paper, key) touched by exactly one member = non-conflicting edit that must survive.
    writers: dict[tuple, set] = {}
    for m, n, key, value in edits:
        writers.setdefault((n, key), set()).add(m)
    last_value = {}
    for m, n, key, value in edits:            # a member's later edit of the same key supersedes its earlier one
        last_value[(m, n, key)] = value
    non_conflicting = [(n, key, last_value[(next(iter(ws)), n, key)]) for (n, key), ws in writers.items() if len(ws) == 1]
    conflicts = sum(1 for ws in writers.values() if len(ws) > 1)

    def local_state(m):
        st = {n: dict(v0[n]) for n in range(K_PAPERS)}
        for mm, n, key, value in edits:
            if mm == m:
                st[n][key] = value
        return st

    # Alternative 1: one library file, whole-file last-writer-wins (last syncer overwrites everything).
    whole_file = local_state(order[-1])
    # Alternative 2: one file per paper, whole-record last-writer-wins among the members who edited it.
    per_record = {n: dict(v0[n]) for n in range(K_PAPERS)}
    for m in order:
        st = local_state(m)
        for n in {nn for mm, nn, _, _ in edits if mm == m}:
            per_record[n] = st[n]

    def lost(state):
        return sum(1 for n, key, value in non_conflicting if state[n].get(key) != value)

    converged = all(f == finals[0] for f in finals[1:])
    rows = []
    for method, lost_n, conv in (("per_field", max(lost(f) for f in finals), converged),
                                 ("whole_file", lost(whole_file), True),
                                 ("per_record", lost(per_record), True)):
        rows.append({"members": members, "seed": seed, "method": method, "edits": len(edits),
                     "non_conflicting": len(non_conflicting), "conflicts": conflicts, "lost": lost_n,
                     "lost_rate": lost_n / max(1, len(non_conflicting)), "converged": int(conv)})
    return rows


# ======================================================================================
# Measure / plot
# ======================================================================================

def measure() -> None:
    import csv

    RESULTS.mkdir(parents=True, exist_ok=True)
    if not (Path(PREV_WORKTREE) / "doi_hunter_web").exists():
        subprocess.run(["git", "worktree", "add", "-f", PREV_WORKTREE, PREV_REV], cwd=REPO, check=True)
    # ---- J
    rows = []
    for t in range(J_TRIALS):
        for version, root in (("previous", PREV_WORKTREE), ("connector", str(REPO))):
            work = tempfile.mkdtemp(prefix="rhJ-")
            code = (f"import json,sys; sys.path.insert(0, {str(REPO / 'benchmarks')!r}); "
                    f"import benchmark_zotero_shared as b; print('RESULT'+json.dumps(b._j_worker({root!r}, {SEED + t}, {work!r})))")
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=work)
            shutil.rmtree(work, ignore_errors=True)
            line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")), None)
            if not line:
                raise RuntimeError(f"J worker failed ({version}, trial {t}):\n{proc.stderr[-3000:]}")
            res = json.loads(line[6:])
            res.update(version=version, trial=t)
            rows.append(res)
            print(f"J trial {t:2d} {version:9s} reflected={res['zotero_edits_reflected']:.2f} "
                  f"kept={res['local_kept']:.2f} leaked={res['feed_leaked']} resync={res['t_resync']:.2f}s")
    with open(RAW_J, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    # ---- K (real engine in this process; every trial in its own folders)
    sys.path.insert(0, str(REPO))
    krows = []
    for members in K_TEAM_SIZES:
        for t in range(K_TRIALS):
            work = Path(tempfile.mkdtemp(prefix="rhK-"))
            krows += _k_trial(members, SEED + 1000 * members + t, work)
            from doi_hunter_web import db
            db.reset_connections()
            shutil.rmtree(work, ignore_errors=True)
        print(f"K members={members}: done")
    with open(RAW_K, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(krows[0]))
        w.writeheader()
        w.writerows(krows)
    print(f"raw results: {RAW_J}, {RAW_K}")


def _paired_tests(a, b) -> dict:
    """Exact sign test (no distribution assumption) + Wilcoxon signed-rank (assumes symmetric differences;
    the skewness of the differences is reported so the assumption can be checked)."""
    import numpy as np
    from scipy import stats

    d = np.asarray(b, float) - np.asarray(a, float)
    nz = d[d != 0]
    out = {"n": len(d), "median_diff": float(np.median(d)), "n_nonzero": int(len(nz))}
    if len(nz) == 0:
        out.update(sign_p=1.0, wilcoxon_p=1.0, diff_skew=0.0)
        return out
    out["sign_p"] = float(stats.binomtest(int((nz > 0).sum()), len(nz), 0.5).pvalue)
    out["wilcoxon_p"] = float(stats.wilcoxon(nz).pvalue) if len(nz) >= 6 else float("nan")
    out["diff_skew"] = float(stats.skew(nz)) if len(np.unique(nz)) > 2 else 0.0
    return out


def _p_label(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def plot() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    plt.rcParams.update({"font.size": FONT_SIZE, "axes.spines.top": False, "axes.spines.right": False,
                         "font.family": "DejaVu Sans", "svg.fonttype": "none"})
    J = pd.read_csv(RAW_J)
    K = pd.read_csv(RAW_K)
    prev, new = (J[J.version == v].sort_values("trial") for v in ("previous", "connector"))
    stats_rows = []

    def describe(name, a, b, unit=""):
        t = _paired_tests(a, b)
        stats_rows.append({"benchmark": "J", "metric": name, "previous_median": float(np.median(a)),
                           "previous_iqr": float(np.subtract(*np.percentile(a, [75, 25]))),
                           "connector_median": float(np.median(b)),
                           "connector_iqr": float(np.subtract(*np.percentile(b, [75, 25]))), "unit": unit, **t})
        return t

    # ---------------- J figure
    fig, axes = plt.subplots(1, 3, figsize=(8.4, 2.7), gridspec_kw={"width_ratios": [2.8, 1.1, 1.4]})
    kinds = list(J_EDITS) + ["local_kept"]
    labels = ["title", "tag +", "tag -", "trash", "new", "local\nkept"]
    x = np.arange(len(kinds))
    ax = axes[0]
    for k, (version, df) in enumerate((("previous", prev), ("connector", new))):
        cols = [f"reflected_{c}" for c in J_EDITS] + ["local_kept"]
        vals = df[cols].to_numpy() * 100
        ax.bar(x + (k - 0.5) * BAR_WIDTH * 1.15, vals.mean(0), BAR_WIDTH, color=COLORS[version],
               label="previous (one-shot import)" if version == "previous" else "smart connector")
    for i, c in enumerate([f"reflected_{c}" for c in J_EDITS] + ["local_kept"]):
        t = describe(c, prev[c].to_numpy(), new[c].to_numpy(), "fraction")
        ax.text(i, 106, "n.s." if t["sign_p"] >= ALPHA else ("***" if t["sign_p"] < 0.001 else "*"),
                ha="center", fontsize=FONT_SIZE - 1)
    ax.set_xticks(x, labels, fontsize=FONT_SIZE - 1)
    ax.set_xlabel("change made in Zotero (local kept = edit made in ReviewHunter)", fontsize=FONT_SIZE - 1)
    ax.set_ylim(0, 115)
    ax.set_ylabel("% of changes applied on re-sync")
    ax.set_title("a  Zotero edits after the first import", loc="left", fontsize=FONT_SIZE + 1)
    ax.legend(frameon=False, fontsize=FONT_SIZE - 1, loc="lower left", bbox_to_anchor=(0, 1.08), ncol=2)

    ax = axes[1]
    for k, (version, df) in enumerate((("previous", prev), ("connector", new))):
        ax.bar(k, df.feed_leaked.mean(), BAR_WIDTH * 2, color=COLORS[version])
        ax.scatter(np.full(len(df), k) + np.random.default_rng(1).uniform(-.08, .08, len(df)), df.feed_leaked,
                   s=4, color="k", zorder=3, linewidths=0)
    t = describe("feed_leaked", prev.feed_leaked.to_numpy(), new.feed_leaked.to_numpy(), "items")
    ax.set_xticks([0, 1], ["previous", "connector"])
    ax.set_ylabel("RSS feed items imported")
    ax.set_title(f"b  library isolation\n{_p_label(t['sign_p'])} (sign test)", loc="left", fontsize=FONT_SIZE + 1)

    ax = axes[2]
    groups = [("t_initial", "initial"), ("t_resync", "re-sync"), ("t_idle", "idle\ncheck")]
    for k, (version, df) in enumerate((("previous", prev), ("connector", new))):
        for i, (col, _) in enumerate(groups):
            v = df[col].dropna().to_numpy()
            if len(v):
                ax.bar(i + (k - 0.5) * BAR_WIDTH * 1.15, np.median(v), BAR_WIDTH, color=COLORS[version])
    for col, _ in groups[:2]:
        describe(col, prev[col].to_numpy(), new[col].to_numpy(), "s")
    ax.set_yscale("log")
    ax.set_xticks(range(3), [g[1] for g in groups])
    ax.set_ylabel("median wall time (s, log)")
    ax.set_title(f"c  speed ({J_USER_ITEMS + J_GROUP_ITEMS} items)", loc="left", fontsize=FONT_SIZE + 1)
    fig.tight_layout()
    for ext in ("png", "pdf", "svg"):
        fig.savefig(RESULTS / f"J_zotero_sync.{ext}", dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)

    # ---------------- K figure
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.5), gridspec_kw={"width_ratios": [2, 1.2]})
    ax = axes[0]
    methods = [("whole_file", "one library file"), ("per_record", "one file per paper"),
               ("per_field", "ReviewHunter (per member, per field)")]
    for i, members in enumerate(K_TEAM_SIZES):
        sub = K[K.members == members]
        ref = sub[sub.method == "per_field"].sort_values("seed").lost_rate.to_numpy()
        for k, (method, label) in enumerate(methods):
            v = sub[sub.method == method].sort_values("seed").lost_rate.to_numpy() * 100
            xpos = i + (k - 1) * BAR_WIDTH * 1.15
            ax.bar(xpos, v.mean(), BAR_WIDTH, color=COLORS[method], label=label if i == 0 else None)
            ax.errorbar(xpos, v.mean(), yerr=v.std(ddof=1), color="k", lw=0.6, capsize=1.5)
            if v.mean() == 0:   # a zero bar is invisible: label it
                ax.text(xpos, 2, "0", ha="center", fontsize=FONT_SIZE - 1, color=COLORS[method])
            if method != "per_field":
                t = _paired_tests(ref * 100, v)
                stats_rows.append({"benchmark": "K", "metric": f"lost_rate members={members} {method} vs per_field",
                                   "previous_median": float(np.median(v)), "connector_median": float(np.median(ref * 100)),
                                   "unit": "% of non-conflicting edits", **t})
        ax.text(i, 104, _p_label(max(r["sign_p"] for r in stats_rows if r["metric"].startswith(f"lost_rate members={members}"))),
                ha="center", fontsize=FONT_SIZE - 1)
    ax.set_xticks(range(len(K_TEAM_SIZES)), [f"{m} members" for m in K_TEAM_SIZES])
    ax.set_ylim(0, 112)
    ax.set_ylabel("non-conflicting edits lost (%)")
    ax.set_title("a  concurrent editing, mean ± SD", loc="left", fontsize=FONT_SIZE + 1)
    ax.legend(frameon=False, fontsize=FONT_SIZE - 1, loc="lower left", bbox_to_anchor=(0, 1.08), ncol=2)

    ax = axes[1]
    pf = K[K.method == "per_field"].groupby("members").agg(converged=("converged", "mean"), conflicts=("conflicts", "mean"),
                                                           lost=("lost", "sum"))
    ax.bar(np.arange(len(pf)), pf.converged * 100, BAR_WIDTH * 2, color=COLORS["per_field"])
    ax.set_xticks(range(len(pf)), [f"{m}\n{r.conflicts:.2f}" for m, r in pf.iterrows()])
    ax.set_xlabel("members / same-field conflicts per trial", fontsize=FONT_SIZE - 1)
    ax.set_ylim(0, 112)
    ax.set_ylabel("trials where all members\nend identical (%)")
    ax.set_title("b  convergence (real engine)", loc="left", fontsize=FONT_SIZE + 1)
    fig.tight_layout()
    for ext in ("png", "pdf", "svg"):
        fig.savefig(RESULTS / f"K_shared_concurrency.{ext}", dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)

    pd.DataFrame(stats_rows).to_csv(RESULTS / "JK_statistics.csv", index=False)
    print(pd.DataFrame(stats_rows)[["benchmark", "metric", "previous_median", "connector_median", "n_nonzero",
                                    "sign_p", "wilcoxon_p", "diff_skew"]].to_string())


if __name__ == "__main__":
    {"measure": measure, "plot": plot}[sys.argv[1] if len(sys.argv) > 1 else "measure"]()
