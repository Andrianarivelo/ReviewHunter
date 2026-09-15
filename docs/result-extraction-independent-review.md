# Independent result-extraction review

The review exercised the public extractor with synthetic sentences, without
editing or querying library records. Eighteen regression cases are preserved in
`tests/test_result_review.py`; all pass after the extraction fixes.

| Failure found during review | Corrected behavior |
| --- | --- |
| `No increase` became an increase | Direction remains unspecified |
| A later endpoint borrowed an earlier endpoint's increase | Its own predicate supplies its direction |
| Negation of one endpoint leaked into the next | Negation is restricted to the applicable predicate |
| Singular prior-study and experimental-purpose clauses became findings | Background and setup clauses are excluded |
| A new subject inherited the first drug factor | Unsupported factor attribution is withheld |
| A decrease of a reduction became a simple decrease | Nested change remains unspecified |
| Administration and withdrawal could be pooled | Withdrawal context remains distinct |
| Acute/chronic, day/night, pregnancy, repeated exposure, or awake/anesthetized conditions pooled | Explicit differing conditions prevent a shared comparison |
| Plural sex terms disappeared | Singular and plural sex labels normalize consistently |

The suite also checks that 24-hour and 48-hour observations remain separate. It
permits conservative exclusion when an experimental context cannot be resolved.
These checks protect selected semantics; they do not establish general extraction
accuracy. Endpoint recognition and contextual qualifiers remain rule-based and
incomplete. The full source sentence remains available for review. Matched
reported fields do not guarantee equivalent experimental protocols, and direction
counts do not estimate effect sizes or statistical significance.

Run with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_result_review.py -q
```

The methodology benchmark was recomputed after the reviewed source stabilized.
Its fingerprint now includes the goal, result, and concept helper modules. Every
metric row was identical to the previous cache: on its five-paper annotated
convenience sample, baseline precision/recall are 0.756/0.831 and current
precision/recall are 1.000/0.761. Refreshed PNG/PDF/SVG exports retain those exact
measurements. The rendered PNG was inspected for legibility and clipping.
