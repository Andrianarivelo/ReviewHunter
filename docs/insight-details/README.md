# Detailed insight-map visual audit

The libraries used here are explicitly synthetic interface fixtures. Their drugs,
signals, procedures and example statements are invented for software validation.
They are not findings from published research and are not accuracy benchmarks.

The optional extended fixture contains 12 papers. It exercises cocaine,
methamphetamine and MDMA identities; Neuropixels and tetrode recordings of spikes
and local field potentials; GCaMP two-photon calcium imaging; dLight fiber
photometry of dopamine; behavioral measures; and densely populated method rows.
The ordinary four-paper fixture remains available for existing regression checks.

Create a new fixture from the repository root:

```powershell
.venv\Scripts\python.exe tools\insight_fixture.py --detailed
```

Set `REVIEWHUNTER_HOME` to the printed `home` directory and
`REVIEWHUNTER_PORT=8775`, then run the app with `--port 8775 --no-browser --no-https`.
Keep the production app and its library untouched. Use the new manifest:

```powershell
.venv\Scripts\python.exe tools\insight_detail_audit.py --fixture <home>\fixture.json
```

The audit verifies the manifest against actual server item IDs and titles before
opening a map. It refuses other ports, non-synthetic libraries and existing output
directories. Each run receives a timestamped directory, preserving earlier
before-and-after artifacts.

For an export-only implementation change, `--exports-only --out <existing-run>`
rechecks the same manifest and refreshes only that run's export files and export
report. It retains all UI screenshots. Other audit directories remain untouched.

Checks cover detailed method identities and categories, source evidence on click,
chip geometry, frequency-dependent font sizes, concept colors and filtering,
single-occurrence words, and distinctive terms absent from the remaining library.
The known singleton is `alliesthesia`; `hysteresis` appears repeatedly only in the
temperature collection. Screenshots cover both light and dark themes at desktop
(1600 by 1000) and laptop (1366 by 768) sizes. Every run writes measured results to
`report.json`. Screenshots require a separate human visual inspection; passing
geometry checks alone does not establish visual quality.

## Validated run: 15 September 2026

The final [audit report](20260915-detailed-v3/report.json) covers 32 screenshots:
eight views at each of the four viewport/theme combinations. All had zero browser
console errors, zero overlapping method chips and no document-width overflow.
Six representative final screenshots and the standalone word and methodology
PNG exports were also visually inspected. Dense rows remain readable; a laptop
viewport scrolls vertically to show their remaining content.

Frequency clouds used a 16 to 76 pixel font range and seven concept colors in
all four configurations. The singleton `alliesthesia` retained one occurrence;
the scoped `hysteresis` term had four occurrences, none in the comparison text,
and log-odds 1.66. The per-paper checks distinguished dLight dopamine signals from
GCaMP calcium signals and rejected a drug self-administration inference from a
fixed-ratio training schedule alone.

All four maps exported valid JSON, rectangular CSV and PNG files. The methodology
CSV retained all 12 papers and 17 detailed drug/signal/tool columns. With the
full-text source and rare filter active, the word CSV retained the union of all
three returned pools: 300 terms. Its PNG included all nine relevant concept
legend labels and the explicit rarity/comparison limitations. A final export-only
refresh verified that the PNG preserves the Georgia font used to measure cloud
placement. The resulting standalone PNG was visually inspected again.

Representative artifacts:

- [Detailed methodology, dark](20260915-detailed-v3/desktop-dark/methods-dense.png)
- [Frequency cloud, dark](20260915-detailed-v3/desktop-dark/words-frequency.png)
- [Single-occurrence terms, dark](20260915-detailed-v3/desktop-dark/words-rare.png)
- [Unusual terms, laptop light](20260915-detailed-v3/laptop-light/words-unusual.png)
- [Standalone word export with legend](20260915-detailed-v3/desktop-dark/exports/words/export-words.png)
- [Complete methods matrix CSV](20260915-detailed-v3/desktop-dark/exports/methods/export-methods.csv)
