# V3 evaluation evidence

Run on 29 October 2026 with `muse-spark-1.3`. This records the actual results of
the `PLAN_V3.md` acceptance work, including incomplete drawings.

## Twenty-four drawing comparison

The source PDF hash was checked against the database adjacent to the recorded
v2 results. Both runs contain the same 24 crops. No third-party source images
or PDF bytes are included in this report.

| Measure | Recorded v2 | V3 final batch attempt |
|---|---:|---:|
| Crops accounted for | 24/24 | 24/24 |
| Buildable geometry | 24/24 (100%) | 16/24 (66.7%) |
| Guarded completed jobs | Not recorded | 4/24 (16.7%) |
| Needs review | Not recorded | 20/24 (83.3%) |
| Required checks: PASS / FAIL / UNVERIFIED | Not comparable | 1,265 / 29 / 126 |
| V2 checks: PASS / FAIL / UNVERIFIED | 916 / 0 / 79 | Not comparable |
| Printed dimensions read | 105 | 105 |
| V2 syntactically traced values | 55 | Not comparable |
| Independently measured source-dimension PASS | Not recorded | 67/105 |
| Exact source labels retained | 198/242 read | 99/216 read |
| Independent critic: pass / fail / absent | Not recorded | 11 / 12 / 1 |
| Reported total tokens, including cached input | 715,040 | 4,707,791 |
| Median seconds per crop | 110.70 | 239.63 |
| 95th percentile seconds per crop | 197.20 | 550.34 |

V3 reported 3,672,465 input tokens, including 2,353,770 cached tokens, and
1,035,326 output tokens. It attempted 35 CAD builds and 30 render operations.
All final-attempt jobs terminated: four succeeded and twenty require review;
none was dropped from the denominator. A critic pass alone cannot complete a
job: required geometry/source checks and exact-version evidence must also pass.

These numbers do **not** demonstrate an improvement in accuracy, speed or cost.
The old build/trace checks were weaker and did not establish independent feature
measurement or human fidelity. The old evaluator did not record its commit or
critic results. V3 catches failures that the previous run did not measure, but
also loses labels and leaves many source measurements unresolved. Human fidelity
annotations remain pending in both datasets.

The final attempt used six saved incomplete drafts and eighteen fresh
generations. Two earlier interrupted attempts are retained in `attempts.json`
and the database. Across all three attempts there are 72 unique jobs: four
succeeded, thirty require review and thirty-eight were cancelled. Their recorded
consumption totals 6,903,682 tokens. Cancelled uncertain requests retain another
359,233 tokens in reservations; these are accounting allowances, not confirmed
provider usage. The final-attempt token count is therefore not the total cost of
this implementation/evaluation exercise.

This is a frozen evaluation snapshot, preceding the final CAD support-face,
bounded component-context and controller refinements. Its results have not been
silently relabelled as results for the final code. Own-drawing acceptance was
rerun under the final verifier. The recorded batch identity is:

```text
manifest:       4dd6ba5f23f1089b7196e5d396ed0b8c22e92eac02fa607ceea925bf73bf7ca5
implementation: c8aab9f943c9099faaa28dca756edbaf4e232db58f2efa464b401ec51b238c81
verifier:       setout-v3-b301541edd7a34a3
```

Private local evidence is in `var/eval-v3-release-batch/eval/`: `manifest.json`,
`results.json`, `summary.json`, `comparison.json`, `baseline.json`,
`attempts.json` and `review.html`. The adjacent `v3.db` contains complete model
and tool traces; the artifact store contains exact source/render/build bytes.

## AI-made objects and memory

Live AI-written WC, vanity and basin code passed fresh, independent STEP
measurement in `var/v3-objects-live/result.json`. Test sizes were explicitly
assumptions. The WC measured 360 × 800 × 650mm, cabinet 800 × 750 × 450mm and
basin 600 × 160 × 400mm in Setout width/height/depth order. Earlier failed
revisions remain in history. Actual renders were inspected for curved open
bowls and recognisable cabinet/toilet form; passing bounds alone was insufficient.

`var/v3-memory-live-checked/result.json` records a live model A/B on a separate
held-out drawing. A synthetic reviewer corrected an unprinted training slab
convention from 100mm to 123mm. With memory off the held-out output used 100mm;
with memory on it used 123mm, still labelled an assumption. Both arms read the
held-out drawing's printed 75mm wall height and exact quote instead of copying
the training drawing's 50mm. This validates correction transfer and provenance,
with a synthetic reviewer; it is not a field study or a claim that a project
owner has reviewed these examples.

## Own-drawing acceptance

Both drawings from `scripts/demo_drawing.py` passed the final current-policy
verification, assembled rendering, independent critique and guarded finish.

| Drawing | Immutable version | Required PASS | Printed dimensions measured | Critic |
|---|---|---:|---:|---|
| Shower section | `e4ca6da36a6a4bf8b17855c6ab035ed5` | 63 | 2/2 | pass |
| Bathroom elevation | `dd8e584fca4c4e33afcd2e7dcbae2da0` | 125 | 2/2 | pass |

Neither has a required FAIL or UNVERIFIED result. Source labels retained are
8/12 for the section and 12/12 for the elevation. Nonrequired limitations and
unprinted assumptions remain visible. The final evidence refresh used verifier
`setout-v3-c91e0312a946d117` and manifest
`2046b990070e7e303e0733e5c9df53ee306c2e8f59169ac1aa0c0476ee7d7511`.
Both exact-version completion guards were rechecked and returned no errors.

These examples were repaired iteratively with directed revision requests and
reused AI-written prototypes, rebuilt with the drawing's current parameters.
They are functional acceptance demonstrations, not a one-shot accuracy score.
Visual inspection rejected earlier block fixtures, primitive fittings and a
floating cabinet before accepting the repaired form for this implementation
check. Human publication acceptance and independent fidelity annotation remain
separate. Failed attempts and earlier policy results are preserved.

`var/eval-v3-final-demo/eval/acceptance.json` records exact job/version/report/
critic IDs. `review.html` embeds the own-source drawings and matched renders.
The final refresh consumed 202,317 tokens across both crops; this excludes the
earlier generation, repair and component-probe runs and must not be quoted as
the cost of producing the finished examples.

See [V3 implementation and validation](V3_IMPLEMENTATION.md) for acceptance
status, commands, automated checks and supported measurement boundaries.
