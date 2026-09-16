"""KLT-002 delay and correction-localization experiment."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister

from .analysis import _decode_joint_key, wilson_interval
from .circuits import AxisName, STATE_SPECS, StateName, _prepare_state, _rotate_for_measurement
from .protocols import FOLLOWUP_PROTOCOL_ID, FOLLOWUP_PROTOCOL_VERSION

FollowupMode = Literal[
    "direct",
    "none",
    "x_only",
    "z_only",
    "both",
    "conditional_shadow",
    "delay_2us",
    "delay_4us",
    "delay_8us",
]

FOLLOWUP_MODES: tuple[FollowupMode, ...] = (
    "direct",
    "none",
    "x_only",
    "z_only",
    "both",
    "conditional_shadow",
    "delay_2us",
    "delay_4us",
    "delay_8us",
)

DELAY_US_BY_MODE: dict[FollowupMode, int] = {
    "delay_2us": 2,
    "delay_4us": 4,
    "delay_8us": 8,
}


@dataclass(frozen=True)
class FollowupSpec:
    """Scientific identity of one KLT-002 physical circuit."""

    mode: FollowupMode
    state: StateName
    axis: AxisName
    eigenvalue: int

    @property
    def name(self) -> str:
        state_slug = self.state.replace("+", "plus").replace("-", "minus")
        return f"klt002__{self.mode}__{state_slug}__{self.axis.lower()}"

    @property
    def delay_us(self) -> int:
        return DELAY_US_BY_MODE.get(self.mode, 0)


def followup_specs() -> list[FollowupSpec]:
    """Return the frozen 54-circuit KLT-002 design in deterministic order."""

    return [
        FollowupSpec(mode, state, axis, eigenvalue)
        for mode in FOLLOWUP_MODES
        for state, axis, eigenvalue in STATE_SPECS
    ]


def _append_mid_circuit_measurements(
    circuit: QuantumCircuit,
    q,
    m0: ClassicalRegister,
    m1: ClassicalRegister,
    optimized: bool,
) -> None:
    if optimized:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure

        circuit.append(MidCircuitMeasure(), [q[0]], [m0[0]])
        circuit.append(MidCircuitMeasure(), [q[1]], [m1[0]])
    else:
        circuit.measure(q[0], m0[0])
        circuit.measure(q[1], m1[0])


def build_followup_circuit(
    spec: FollowupSpec,
    *,
    optimized_mid_circuit_measurement: bool = False,
) -> QuantumCircuit:
    """Build one frozen KLT-002 circuit.

    Barriers place the delay/control window after both Bell measurements and
    before the final basis rotation. They are retained for scheduling semantics,
    not treated as physical operations.
    """

    q = QuantumRegister(3, "q")
    m0 = ClassicalRegister(1, "m0")
    m1 = ClassicalRegister(1, "m1")
    out = ClassicalRegister(1, "out")
    circuit = QuantumCircuit(q, m0, m1, out, name=spec.name)
    circuit.metadata = {
        "protocol_id": FOLLOWUP_PROTOCOL_ID,
        "protocol_version": FOLLOWUP_PROTOCOL_VERSION,
        **asdict(spec),
        "delay_us": spec.delay_us,
    }
    if spec.mode == "direct":
        _prepare_state(circuit, q[2], spec.state)
        _rotate_for_measurement(circuit, q[2], spec.axis)
        circuit.measure(q[2], out[0])
        return circuit

    _prepare_state(circuit, q[0], spec.state)
    circuit.h(q[1])
    circuit.cx(q[1], q[2])
    circuit.cx(q[0], q[1])
    circuit.h(q[0])
    _append_mid_circuit_measurements(
        circuit, q, m0, m1, optimized_mid_circuit_measurement
    )
    circuit.barrier(*q)

    if spec.mode in {"x_only", "both"}:
        with circuit.if_test((m1, 1)):
            circuit.x(q[2])
    if spec.mode in {"z_only", "both"}:
        with circuit.if_test((m0, 1)):
            circuit.z(q[2])
    if spec.mode == "conditional_shadow":
        # Preserve two broadcasts/branches while keeping their gates off q2.
        with circuit.if_test((m1, 1)):
            circuit.x(q[0])
        with circuit.if_test((m0, 1)):
            circuit.z(q[1])
    if spec.delay_us:
        circuit.delay(spec.delay_us, q[2], unit="us")

    circuit.barrier(*q)
    _rotate_for_measurement(circuit, q[2], spec.axis)
    circuit.measure(q[2], out[0])
    return circuit


def build_followup_experiment(
    *,
    optimized_mid_circuit_measurement: bool = False,
) -> tuple[list[FollowupSpec], list[QuantumCircuit]]:
    specs = followup_specs()
    circuits = [
        build_followup_circuit(
            spec,
            optimized_mid_circuit_measurement=optimized_mid_circuit_measurement,
        )
        for spec in specs
    ]
    return specs, circuits


def _missing_correction_bits(spec: FollowupSpec, m0: int, m1: int) -> tuple[int, int]:
    """Return missing (X, Z) exponents to apply in offline analysis."""

    if spec.mode in {"direct", "both"}:
        return 0, 0
    missing_x = 0 if spec.mode == "x_only" else m1
    missing_z = 0 if spec.mode == "z_only" else m0
    return missing_x, missing_z


def followup_offline_flip(spec: FollowupSpec, m0: int, m1: int) -> int:
    missing_x, missing_z = _missing_correction_bits(spec, m0, m1)
    if spec.axis == "X":
        return missing_z
    if spec.axis == "Z":
        return missing_x
    if spec.axis == "Y":
        return missing_x ^ missing_z
    raise ValueError(f"Unknown axis: {spec.axis}")


def score_followup_counts(
    spec: FollowupSpec, counts: dict[str, int]
) -> tuple[int, int]:
    desired = 0 if spec.eigenvalue == 1 else 1
    successes = 0
    total = 0
    if not counts:
        raise ValueError(f"No counts found for {spec.name}")
    for key, count in counts.items():
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError(f"Invalid count for {spec.name}: {key}={count!r}")
        m0, m1, outcome = _decode_joint_key(key)
        outcome ^= followup_offline_flip(spec, m0, m1)
        successes += count if outcome == desired else 0
        total += count
    return successes, total


def analyze_followup_counts(
    counts_by_name: dict[str, dict[str, int]],
    *,
    bootstrap_draws: int = 20_000,
    seed: int = 260915,
) -> dict:
    """Analyze KLT-002 using the preregistered missing-correction rules."""

    specs = followup_specs()
    expected = {spec.name for spec in specs}
    received = set(counts_by_name)
    if expected != received:
        raise ValueError(
            f"Incomplete follow-up data; missing={sorted(expected - received)}, "
            f"extra={sorted(received - expected)}"
        )

    rows: list[dict] = []
    for spec in specs:
        successes, shots = score_followup_counts(spec, counts_by_name[spec.name])
        low, high = wilson_interval(successes, shots)
        rows.append(
            {
                **asdict(spec),
                "circuit": spec.name,
                "delay_us": spec.delay_us,
                "successes": successes,
                "shots": shots,
                "fidelity": successes / shots,
                "ci95_low": low,
                "ci95_high": high,
            }
        )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["mode"]].append(row)
    rng = np.random.default_rng(seed)
    summaries: list[dict] = []
    axis_summaries: list[dict] = []
    for mode in FOLLOWUP_MODES:
        group = grouped[mode]
        if len(group) != 6:
            raise ValueError(f"Expected six states for {mode}, got {len(group)}")
        boot = np.zeros(bootstrap_draws, dtype=float)
        for row in group:
            boot += rng.binomial(
                row["shots"], row["fidelity"], size=bootstrap_draws
            ) / row["shots"]
        boot /= 6
        low, high = np.quantile(boot, [0.025, 0.975])
        summaries.append(
            {
                "mode": mode,
                "mean_fidelity": float(np.mean([row["fidelity"] for row in group])),
                "ci95_low": float(low),
                "ci95_high": float(high),
                "shots_per_state": sorted({row["shots"] for row in group}),
            }
        )
        for axis in ("X", "Y", "Z"):
            axis_rows = [row for row in group if row["axis"] == axis]
            mean = float(np.mean([row["fidelity"] for row in axis_rows]))
            axis_summaries.append(
                {
                    "mode": mode,
                    "axis": axis,
                    "mean_fidelity": mean,
                    "pauli_transfer_diagonal": 2.0 * mean - 1.0,
                }
            )

    return {
        "protocol_id": FOLLOWUP_PROTOCOL_ID,
        "protocol_version": FOLLOWUP_PROTOCOL_VERSION,
        "per_state": rows,
        "summary": summaries,
        "axis_summary": axis_summaries,
        "bootstrap": {"draws": bootstrap_draws, "seed": seed},
    }
