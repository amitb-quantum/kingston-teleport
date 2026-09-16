# KLT-002 results: correction-path and timing controls

- Run date: 2026-09-16 UTC
- Backend: `ibm_kingston`
- Physical chain: `[147, 148, 149]`
- IBM job: `dal13ss62pvc739q7gig`
- IBM-reported usage: **10 QPU seconds**
- Protocol: `KLT-002 v1.0.0`

## Headline result

The experiment did not localize the KLT-001 anisotropy to either the live X or
live Z correction. X-only, Z-only, both-correction, and two-branch shadow modes
had nearly identical six-state means (`0.6781`–`0.6810`) and similar
axis-resolved structure: high X fidelity and near-random Y/Z fidelity.

The preregistered shadow criterion was met. Its absolute difference from the
both-correction mode was `0.0146`, `0.0283`, and `0.0098` on X, Y, and Z,
respectively—all within the frozen `0.03` margin. This supports a limited
phenomenological conclusion: two live conditional branches plus their schedule
were sufficient to reproduce the both-correction result even when their gates
were moved off the destination qubit. It does not identify a microscopic cause.

None of the explicit 2/4/8 μs destination delays resembled the both-correction
channel within the preregistered margin. The accurate schedules also show why
these were brackets rather than matches: both corrections added about `0.768`
μs relative to the no-branch circuit for the matched `|+⟩` example, while the
smallest explicit delay added exactly `2.000` μs.

![KLT-002 mode and axis results](derived/klt002_result_summary.png)

## Mode-level results

| Mode | Six-state mean | Bootstrap 95% interval | X | Y | Z |
|---|---:|---:|---:|---:|---:|
| Direct baseline | 0.9948 | [0.9922, 0.9971] | 0.9932 | 0.9941 | 0.9971 |
| Neither live correction (`none`) | 0.7503 | [0.7357, 0.7646] | 0.9521 | 0.6367 | 0.6621 |
| X-only | 0.6794 | [0.6641, 0.6947] | 0.9355 | 0.5410 | 0.5615 |
| Z-only | 0.6781 | [0.6628, 0.6930] | 0.9502 | 0.5439 | 0.5400 |
| Both corrections | 0.6797 | [0.6644, 0.6947] | 0.9521 | 0.5361 | 0.5508 |
| Conditional shadow | 0.6810 | [0.6660, 0.6963] | 0.9375 | 0.5645 | 0.5410 |
| 2 μs delay | 0.6367 | [0.6198, 0.6536] | 0.5635 | 0.6846 | 0.6621 |
| 4 μs delay | 0.4743 | [0.4574, 0.4912] | 0.4561 | 0.3076 | 0.6592 |
| 8 μs delay | 0.4609 | [0.4440, 0.4779] | 0.3740 | 0.3516 | 0.6572 |

The classical measure-and-prepare threshold is `2/3`. The both-correction point
estimate was above it, but its interval included it; KLT-002 therefore did not
establish above-classical average live-corrected fidelity. The no-live-control
mode exceeded the threshold, although it was substantially below KLT-001's
offline estimate and must not be treated as a direct replication across a
different calibration and compiled schedule.

## Preregistered decisions

| Hypothesis | Frozen rule | Outcome |
|---|---|---|
| H1: correction localization | X-only retains X more strongly than Z-only | **Not supported**: 0.9355 versus 0.9502 |
| H2: combination cost | Both mean below both single-correction means | **Not supported**: 0.6797 versus 0.6794 and 0.6781 |
| H3: idle-only sufficiency | One delay within 0.03 of both on every axis | **Not supported**: best maximum axis discrepancy was 0.3887 |
| H4: branch-overhead sufficiency | Shadow within 0.03 of both on every axis | **Supported descriptively**: maximum discrepancy 0.0283 |

These are protocol-defined descriptive decisions, not multiplicity-adjusted
confirmatory tests. “Supported” for H4 means the control was sufficient under
the frozen margin; it is not proof that controller overhead is the unique cause.

## Accurate scheduler timing

IBM returned all 54 requested scheduler records. The table below uses the
matched `|+⟩` circuit from every mode. Timing is converted from `dt` using the
archived Kingston value `dt = 4 ns`. “Wait after MCM” is the start of the first
q149 measurement pulse minus the end of the latest optimized mid-circuit
capture. “Increment” subtracts the no-branch wait.

| Mode | Circuit duration (μs) | Wait after MCM (μs) | Increment vs none (μs) |
|---|---:|---:|---:|
| Direct | 11.160 | — | — |
| None | 12.820 | 0.436 | 0.000 |
| X-only | 13.356 | 0.972 | 0.536 |
| Z-only | 13.368 | 0.984 | 0.548 |
| Both | 13.588 | 1.204 | 0.768 |
| Conditional shadow | 13.444 | 1.060 | 0.624 |
| 2 μs delay | 14.820 | 2.436 | 2.000 |
| 4 μs delay | 16.820 | 4.436 | 4.000 |
| 8 μs delay | 20.820 | 8.436 | 8.000 |

The explicit delays show the expected Z-preserving, transverse-axis-sensitive
pattern rather than the X-preserving pattern of the conditional modes. At 4
and 8 μs the X/Y Pauli-transfer diagonals become negative, suggesting coherent
phase evolution in addition to loss of contrast. The data reject a simple
“ordinary destination idling at 2–8 μs” explanation; they do not distinguish
controller broadcast, synchronization, measurement interaction, crosstalk, or
other schedule-dependent mechanisms.

## Relation to KLT-001

KLT-002's both-correction channel reproduces the qualitative KLT-001 pattern:
X is preserved while Y and Z approach random. Its six-state point estimate
(`0.6797`) is 0.0223 above KLT-001 dynamic (`0.6574`), with overlapping
intervals. The KLT-002 no-branch/offline mean (`0.7503`) is much lower than the
KLT-001 offline value (`0.9512`). Between the runs IBM recorded a different
calibration timestamp, and KLT-002 added barriers that freeze its measurement,
control, and delay window. These differences prevent attributing the cross-run
change to calibration alone or to circuit structure alone.

## Limitations

- One backend, one qubit chain, one calibration snapshot, and one hardware job.
- Modes were submitted in frozen deterministic blocks, not randomized or
  interleaved; very short-timescale drift cannot be separated from mode order.
- Each circuit used 512 shots. Bootstrap intervals reflect shot uncertainty,
  not calibration, chain, or day-to-day variability.
- No error mitigation was used.
- The shadow schedule was close to, but not identical to, the both-correction
  schedule (`0.624` versus `0.768` μs incremental wait in the displayed pair).
- The explicit-delay grid started at 2 μs and therefore did not numerically
  match the observed 0.5–0.8 μs conditional increments.
- Scheduler duration describes the executed circuit schedule, not billable
  usage. IBM's job metrics are the authoritative 10-QPU-second usage record.
- Offline correction reconstructs measurement statistics; it does not deliver
  a corrected quantum state for downstream coherent computation.
- The direct circuits remain a transpiled computational-basis SPAM/readout
  baseline, not an independent six-state state-preparation benchmark.

## Reproduce and verify offline

```bash
kingston-teleport verify-followup
kingston-teleport report-followup
```

`verify-followup` checks all 11 raw artifact hashes, regenerates the complete
analysis exactly from committed counts, confirms 512 shots for each of 54
circuits, verifies every timing record, and reconciles the job ID and 10-second
usage without IBM credentials. The derived directory has its own checksum
manifest. No additional QPU work is required to reproduce this report.
