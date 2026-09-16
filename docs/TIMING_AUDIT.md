# Timing audit: what can and cannot be matched

Audit date: 2026-09-15. This audit contacted IBM only for backend and historical
job metadata; it submitted no QPU work.

## Source circuits versus transpiled ISA

KLT-002 source circuits put a barrier after the two Bell measurements and a
second barrier before the output-basis rotation. The explicit 2, 4, and 8 μs
delays occupy that window on the logical output. The read-only preflight
transpiles all circuits and rejects any initial/final layout other than
`[147, 148, 149]` or any disappeared explicit delay. It archives the exact ISA
circuits as QPY; source diagrams alone are not evidence of physical placement.

The live 2026-09-15 target reported `dt = 4 ns`, approximately 32 ns for X/SX
on q149, 68 ns for CZ on each selected edge, 1.38 μs for optimized
mid-circuit measurement on q147/q148, and 2.18 μs for ordinary q149 measurement.
Qiskit's dynamic-circuit duration data reported a 3.076 μs terminal-measure
value in its scheduling model. Values can drift with calibration and are
recorded again by each preflight.

## Why exact matching is unavailable for KLT-001

IBM's target and dynamic scheduling tables did not expose a complete duration
for `if_else`. More importantly, accurate dynamic-circuit timing is returned
only when a Sampler submission requests experimental `scheduler_timing=True`.
The three completed KLT-001 jobs predate this follow-up and contain only circuit
metadata, not the `compilation.scheduler_timing` record. Their feed-forward
latency therefore cannot be reconstructed exactly from the archived jobs.

Ordinary circuit timeline drawings are also not an accurate substitute for a
dynamic-circuit schedule. The KLT-002 delays are consequently described only
as a 0/2/4/8 μs **bracket**. None is labeled delay-matched.

## Future hardware requirement

The gated KLT-002 submission path requests scheduler timing and archives result
metadata. IBM describes scheduler timing as circuit scheduling information;
it is not the account's billable QPU usage. The result bundle must separately
retain IBM job metrics and `usage.quantum_seconds`.

## KLT-002 returned timing

The subsequently authorized KLT-002 job returned scheduler timing for all 54
circuits. With `dt = 4 ns`, the matched `|+⟩` circuits showed the following
incremental wait from the end of optimized mid-circuit capture to the start of
q149 readout, relative to the no-branch circuit:

- X-only: 0.536 μs
- Z-only: 0.548 μs
- both corrections: 0.768 μs
- conditional shadow: 0.624 μs
- explicit delays: exactly 2.000, 4.000, and 8.000 μs

Thus the delays were genuine brackets but were all longer than the observed
conditional increments. The complete event text remains archived in
`scheduler_timing_metadata.json`; the reproducible extraction is in
`followup_reporting.py`. These schedule durations are not billing quantities.

Official IBM references:

- [Retrieve accurate dynamic-circuit timing](https://quantum.cloud.ibm.com/docs/en/guides/qiskit-runtime-circuit-timing)
- [Visualize circuit timing](https://quantum.cloud.ibm.com/docs/en/guides/visualize-circuit-timing)
- [Estimate job execution time](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time)
- [Dynamic-circuit instruction durations API](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.47/transpiler-passes-scheduling-dynamic-circuit-instruction-durations)
