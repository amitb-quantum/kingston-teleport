# KLT-002 v1.0.0 preregistration

## Status and separation from KLT-001

This document freezes the design of a prospective follow-up to KLT-001. It was
written after inspecting KLT-001 and before any KLT-002 hardware submission.
Local ideal simulation and read-only backend/transpilation audits are allowed
before review; hardware counts are not. KLT-001's hypothesis, counts, analysis,
and interpretation remain unchanged.

## Question

KLT-001 found high offline-corrected teleportation fidelity but strongly
axis-dependent live-corrected fidelity. KLT-002 asks two narrower questions:

1. Which live correction path—`X^m1`, `Z^m0`, or their combination—is
   associated with the loss?
2. Can an explicit destination-qubit idle of 0, 2, 4, or 8 μs reproduce a
   comparable axis-dependent loss when both corrections are applied offline?

The delay sweep is a **latency-bracketing control**, not a latency-matched
control. Public target data do not expose a complete `if_else` feed-forward
duration, and the KLT-001 jobs did not request scheduler-timing metadata.

## Frozen hardware and sampling design

- Backend: `ibm_kingston`
- Instance: `QEC`
- Logical-to-physical order: input, ancilla, output = `[147, 148, 149]`
- States: `|0⟩`, `|1⟩`, `|+⟩`, `|−⟩`, `|+i⟩`, `|−i⟩`
- Shots: 512 per physical circuit
- Physical circuits: 9 modes × 6 states = 54
- Executions: 27,648
- Error mitigation: none
- Runtime session: none
- Jobs: exactly one Sampler job; no automatic retry or resubmission
- Server cap: 20 QPU seconds
- IBM quick estimate: `2 + 0.00035 × 27,648 = 11.6768` QPU seconds,
  assuming no mitigation and no custom repetition delay

The submission command requires a literal `--submit`. The workflow rejects
more than 512 shots, a cap above 20 seconds, or a quick estimate above the
requested cap.

## Frozen physical modes

All teleportation modes use the same state preparation, Bell pair, Bell-basis
transform, two optimized mid-circuit measurements, register order
`[m0, m1, out]`, and final matching-basis output measurement.

| Mode | Operations after Bell measurement | Offline correction used for scoring |
|---|---|---|
| `direct` | Prepare/measure output only | None |
| `none` | No conditional gate | `X^m1 Z^m0` |
| `x_only` | Live `X^m1` | Missing `Z^m0` |
| `z_only` | Live `Z^m0` | Missing `X^m1` |
| `both` | Live `X^m1`, then `Z^m0` | None |
| `conditional_shadow` | Two live branches on measured q0/q1; output waits | `X^m1 Z^m0` |
| `delay_2us` | Explicit 2 μs output delay | `X^m1 Z^m0` |
| `delay_4us` | Explicit 4 μs output delay | `X^m1 Z^m0` |
| `delay_8us` | Explicit 8 μs output delay | `X^m1 Z^m0` |

`conditional_shadow` preserves two conditional broadcasts/branches but places
their `X` and `Z` gates on the already measured input and ancilla, respectively.
It is a control for branch/control overhead, not an assertion that its timing
equals the two output corrections.

Barriers freeze the control/delay window after both mid-circuit measurements
and before the output-basis rotation. Transpilation must preserve both the
initial and final `[147, 148, 149]` layout. Each explicit delay must remain on
physical output qubit 149 in the ISA circuit.

## Frozen scoring rules

For output measurement bit `b`, the offline flip is:

| Measurement axis | Flip from a missing `X^x Z^z` correction |
|---|---|
| X | `z` |
| Z | `x` |
| Y | `x XOR z` |

Here `x=m1` when the X correction is missing and zero otherwise; `z=m0` when
the Z correction is missing and zero otherwise. A shot succeeds when the
corrected bit equals 0 for the +1 eigenstate and 1 for the −1 eigenstate.

The primary endpoint for each mode is the unweighted mean of the six state
fidelities. Secondary endpoints are the X-, Y-, and Z-axis pair means and their
Pauli-transfer diagonal `2F−1`. Per-state Wilson intervals and a deterministic
20,000-draw parametric bootstrap (seed `260915`) are descriptive uncertainty
summaries. No multiplicity-adjusted confirmatory p-values are claimed.

## Hypotheses and decision rules

The KLT-001 observation motivates, but does not guarantee, the following
directional expectations:

- **H1, correction localization:** `x_only` will retain the KLT-001-like
  X-axis performance more strongly than `z_only`; Y may be sensitive to either
  missing or live path. Report all three axes and the full six-state mean.
- **H2, combination cost:** `both` may underperform each single-correction
  mode. Support is descriptive only if the `both` mean is below both single
  modes and the same ordering is visible in at least two axes.
- **H3, idle-only explanation:** if one explicit-delay mode approximates the
  `both` mode within 0.03 absolute fidelity on every axis, ordinary output
  idling remains a sufficient phenomenological explanation. Otherwise the
  delay-only model is not sufficient under this sweep. This is not a
  microscopic causal test.
- **H4, branch-overhead explanation:** if `conditional_shadow` approximates
  `both` within 0.03 on every axis, two-branch overhead remains a sufficient
  phenomenological explanation. Otherwise it is not sufficient under this
  control.

The 0.03 equivalence tolerance is a preregistered descriptive margin, not a
powered non-inferiority bound. Outcomes can constrain explanations without
identifying a unique controller or hardware mechanism.

## Preflight acceptance criteria

Hardware submission is permitted only after a reviewer confirms all of these:

1. Backend is operational and exposes `measure_2`, `if_else`, `cz`, and delay.
2. `measure_2` is supported on physical 147 and 148, and native CZ connectivity
   exists for 147–148 and 148–149.
3. All 54 circuits transpile; their initial and final layouts equal the frozen
   physical mapping; explicit delays survive on output qubit 149.
4. The exact ISA circuits are archived in QPY and the preflight records backend,
   calibration, software, circuit metrics, and the usage estimate.
5. Tests and `verify-results` pass without credentials.
6. The command includes exactly 512 shots, a 20-second cap, and `--submit`.

Any mismatch stops the run. A failed, cancelled, or partial job is archived and
reported; it is not automatically resubmitted. Changing a scientific design
choice requires a new protocol version before seeing hardware outcomes.

## Timing metadata and interpretation

An authorized hardware job must request IBM experimental
`scheduler_timing=True` and archive every PUB's compilation timing metadata.
That metadata is an on-device schedule description, not IBM billing usage.
IBM-reported `quantum_seconds` remains the usage record.

Even with timing metadata, the explicit delays remain bracketing controls
unless IBM's returned timing establishes an appropriate matching quantity.
Cloud queue and network wall time are outside the qubit coherence window and
must not be interpreted as circuit latency.

## Reporting and stopping rules

Report every mode, state, axis, count table, interval, timing record, job ID,
final status, and IBM-reported QPU seconds, including null or adverse results.
Do not select a delay after seeing results and relabel it “matched.” Do not run
additional hardware diagnostics from this job without a new preregistration
and separate user authorization.
