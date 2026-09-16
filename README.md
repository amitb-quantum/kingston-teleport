# Kingston Live Teleportation

A reproducible, QPU-budget-capped test of real-time classical feed-forward on
IBM Quantum's 156-qubit `ibm_kingston` Heron R2 processor.

The experiment teleports the six Pauli eigenstates over a fixed three-qubit
chain. It compares four estimators:

| Estimator | What it measures |
|---|---|
| `direct` | State preparation and measurement floor on the output qubit |
| `dynamic` | Teleportation with mid-circuit measurement and on-device `if_else` corrections |
| `uncorrected` | Teleportation with the corrections omitted; negative control |
| `offline` | The same uncorrected shots corrected after execution from the Bell bits |

The six input states form a spherical 2-design, so their equally weighted mean
fidelity estimates the channel-average teleportation fidelity. The principal
reference is the classical measure-and-prepare limit of `2/3`.

## Why this is interesting

The network latency from a laptop to IBM Quantum is seconds, but it is absent
from the active circuit. Kingston's local control electronics perform the Bell
measurements, evaluate the conditional branches, and correct the still-active
output qubit. This is the restricted cloud-access analogue of the low-latency
QPU/classical feedback loop targeted by tightly coupled systems such as
NVQLink.

The matched offline estimator separates two questions:

1. Did the teleportation correlations survive?
2. What additional fidelity cost is associated with acting on them in real time?

## Safety and QPU budget

- `simulate` and `preflight` never submit QPU work.
- Hardware submission requires the literal `--submit` flag.
- The code refuses a requested cap above 25 QPU-seconds.
- IBM's server-side `max_execution_time` is set to the same cap.
- The default design is one Sampler job: 18 circuits × 1,024 shots.
- No Runtime session and no mitigation expansion are used.
- The exact IBM-reported `quantum_seconds` value is saved after completion.

The local duration estimate is a budget tripwire, not a promise about billed
usage. IBM's target does not publish an `if_else` duration, so the fallback
serially sums published instruction durations and reserves 500 μs for every
conditional. The server-side cap is the authoritative guard.

## Setup

```bash
conda env create -f environment.yml
conda activate kingston-teleport
```

The IBM credential is read from the normal Qiskit account store; it is never
copied into this repository.

## Workflow

Run the ideal simulator and tests:

```bash
pytest
kingston-teleport simulate --shots 2048
```

Perform a live, read-only backend audit and transpilation:

```bash
kingston-teleport preflight \
  --backend ibm_kingston \
  --instance QEC \
  --physical-qubits 147 148 149 \
  --shots 1024
```

Only after inspecting the emitted preflight JSON, submit the frozen design:

```bash
kingston-teleport run \
  --backend ibm_kingston \
  --instance QEC \
  --physical-qubits 147 148 149 \
  --shots 1024 \
  --max-qpu-seconds 25 \
  --submit
```

If a client process is interrupted after submission, recover from the saved
run directory:

```bash
kingston-teleport resume --run-dir runs/<timestamp>
```

An optional four-circuit exploratory diagnostic independently tests the two
Bell registers against conditional X and Z corrections. It is separate from
the preregistered analysis and has its own 8-QPU-second hard cap:

```bash
kingston-teleport diagnose --shots 512 --max-qpu-seconds 8 --submit
```

If the isolated paths pass but the full two-correction protocol is asymmetric,
the final exploratory control compares both conditional orders under a 5-second
hard cap:

```bash
kingston-teleport diagnose-sequence --shots 512 --max-qpu-seconds 5 --submit
```

## Outputs

Each run is immutable and timestamped under `runs/`. It contains:

- configuration and software versions;
- backend status, calibration timestamp, and transpiled circuit metrics;
- job ID saved immediately after submission;
- joint register counts for reproducible offline correction;
- per-state fidelities with Wilson intervals;
- stratified-bootstrap mean fidelities;
- a publication-ready comparison figure;
- IBM job metrics and reported QPU usage.

Raw run artifacts are ignored by Git by default. Publish a deliberately chosen,
redacted result bundle rather than credentials or an uncontrolled collection of
service metadata.

## Scientific scope

This is a single-backend, single-chain characterization—not evidence that all
Kingston qubits or all calibration periods behave identically. The dynamic and
offline estimators also differ operationally: offline correction validates the
classical correlations but cannot make the teleported state available to later
quantum gates. See [`PREREGISTRATION.md`](PREREGISTRATION.md) for the frozen
hypotheses, endpoints, and decision rules.
