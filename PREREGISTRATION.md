# Preregistration: Kingston live-feed-forward teleportation

Date: 2026-09-15
Protocol version: 0.1.0

## Research question

Can IBM Kingston's dynamic-circuit control path preserve quantum teleportation
fidelity above the classical `2/3` limit when Bell-measurement outcomes are
acted upon inside the active circuit, and what fidelity penalty is attributable
to live feed-forward relative to offline correction of the same correlations?

## Frozen design

- Backend: `ibm_kingston`
- Physical chain: `[147, 148, 149]`
- Logical order: input, Bell ancilla, teleported output
- Inputs: `|0>`, `|1>`, `|+>`, `|->`, `|+i>`, `|-i>`
- Measurement: each state's matching Pauli axis
- Physical modes: direct, dynamic, uncorrected
- Derived mode: offline correction of uncorrected joint outcomes
- Shots: 1,024 per physical circuit
- Total physical circuits: 18
- Submission: one Sampler V2 job
- Server-side maximum QPU usage: 25 seconds
- Mitigation: none
- Runtime session: none

The preflight may block submission if the backend is non-operational, required
instructions are unavailable, the selected topology is invalid, optimized
mid-circuit measurement is unavailable on the two Bell-measured qubits, or the
estimated raw circuit duration exceeds half of the server-side budget.
Because IBM does not publish an `if_else` duration in the backend target, the
fallback estimate serially sums all published durations and assigns a
conservative 500 μs allowance to each conditional.

## Endpoints

### Primary

Equally weighted six-state mean fidelity for `dynamic`, with a stratified
parametric-bootstrap 95% interval. The main decision is whether the lower bound
exceeds `2/3`.

### Secondary

1. Dynamic minus uncorrected mean fidelity.
2. Dynamic minus offline-corrected mean fidelity.
3. Offline-corrected mean fidelity relative to `2/3`.
4. Dynamic fidelity split by Pauli axis to expose basis-specific control error.

### Sanity checks

- Ideal simulation: direct, dynamic, and offline means ≥ 0.99.
- Ideal simulation: uncorrected mean within 0.03 of 0.5.
- On hardware: direct should exceed uncorrected.

## Analysis

For each state, success is the probability of measuring its expected Pauli
eigenvalue. The offline estimator flips the final measurement bit according to
the measured teleportation corrections:

- Z readout: flip for Bell bit `m1` (X correction).
- X readout: flip for Bell bit `m0` (Z correction).
- Y readout: flip for `m0 XOR m1`.

Per-state intervals use Wilson score intervals. Mean intervals use a fixed-seed,
20,000-draw stratified parametric bootstrap: each state is independently
resampled from its observed binomial rate and the six rates are averaged.

No state, circuit, or shot is removed after observing results. A failed or
server-canceled job is reported as such and is not silently resubmitted.

## Interpretation boundaries

Passing the `2/3` threshold supports above-classical average state transfer on
this chain at this calibration snapshot. It does not demonstrate fault
tolerance, arbitrary CPU/GPU callbacks, quantum advantage, or fleet-wide IBM
performance. A live-feedback penalty relative to offline correction can include
conditional latency, extra gate error, decoherence, scheduling, and calibration
effects; this experiment does not identify a unique microscopic cause.
