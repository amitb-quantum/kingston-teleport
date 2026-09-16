# Future replication protocol — design only

No replication described here has been submitted. Each requires a new review
and explicit authorization. It must not be appended automatically to KLT-002.

## Spatial replication

The goal is to test whether a result depends on `[147, 148, 149]`. On the day
of a future run, take one live calibration snapshot and enumerate every
three-distinct-qubit path `(input, ancilla, output)` that:

1. is disjoint from `{147, 148, 149}`;
2. has native CZ support on input–ancilla and ancilla–output; and
3. supports optimized mid-circuit measurement on input and ancilla.

For each eligible chain, record both CZ error probabilities, all three readout
errors, and all three T2 values. Compute the frozen score

```text
sum(two CZ errors)
+ sum(three readout errors)
+ sum over three qubits of (8 microseconds / T2)
```

Select the lowest score; break an exact score tie by lexicographic qubit tuple.
The pure implementation is `select_replication_chain` in `replication.py`.
Archive the full candidate table and selected chain before retrieving counts.
Do not substitute a visually preferred chain after observing results.

Run the frozen KLT-001 18-circuit design at 512 shots per circuit, without
mitigation, as one job with a 15-QPU-second server cap. The IBM quick estimate
is `2 + 0.00035 × 9,216 = 5.2256` seconds. Request scheduler timing and archive
the same provenance fields as KLT-002. This is a tightly capped replication,
not a search over chains.

## Temporal replication

The goal is to test sensitivity to calibration time while holding the chain
fixed. Use `ibm_kingston` and `[147, 148, 149]` no sooner than seven full days
after the KLT-002 hardware calibration timestamp. Before submission, archive a
fresh read-only calibration and ISA preflight.

Run the same frozen KLT-001 18-circuit design, 512 shots per circuit, no
mitigation, one job, and a 15-QPU-second cap. Do not change states, corrections,
layout, estimator, or bootstrap rules in response to the spatial result.

## Reporting

Report the original and replication estimates separately before any pooled
summary. Calibration-time and chain variation are not binomial shot noise; do
not reuse within-job Wilson/bootstrap intervals as evidence of cross-run
repeatability. A pooled or hierarchical analysis requires its own declared
analysis plan. Failed or partial jobs remain part of the record and are not
automatically replaced.
