"""Circuit construction for six-state quantum teleportation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister

StateName = Literal["0", "1", "+", "-", "+i", "-i"]
AxisName = Literal["X", "Y", "Z"]
ModeName = Literal["direct", "dynamic", "uncorrected"]


@dataclass(frozen=True)
class CircuitSpec:
    """Scientific identity of one physical circuit."""

    mode: ModeName
    state: StateName
    axis: AxisName
    eigenvalue: int

    @property
    def name(self) -> str:
        state_slug = self.state.replace("+", "plus").replace("-", "minus")
        return f"{self.mode}__{state_slug}__{self.axis.lower()}"


STATE_SPECS: tuple[tuple[StateName, AxisName, int], ...] = (
    ("0", "Z", +1),
    ("1", "Z", -1),
    ("+", "X", +1),
    ("-", "X", -1),
    ("+i", "Y", +1),
    ("-i", "Y", -1),
)


def experiment_specs() -> list[CircuitSpec]:
    """Return the frozen 18-circuit design in deterministic order."""

    return [
        CircuitSpec(mode, state, axis, eigenvalue)
        for mode in ("direct", "dynamic", "uncorrected")
        for state, axis, eigenvalue in STATE_SPECS
    ]


def _prepare_state(circuit: QuantumCircuit, qubit, state: StateName) -> None:
    if state == "0":
        return
    if state == "1":
        circuit.x(qubit)
    elif state == "+":
        circuit.h(qubit)
    elif state == "-":
        circuit.x(qubit)
        circuit.h(qubit)
    elif state == "+i":
        circuit.h(qubit)
        circuit.s(qubit)
    elif state == "-i":
        circuit.h(qubit)
        circuit.sdg(qubit)
    else:  # pragma: no cover - protected by the literal type and tests
        raise ValueError(f"Unknown state: {state}")


def _rotate_for_measurement(
    circuit: QuantumCircuit, qubit, axis: AxisName
) -> None:
    if axis == "Z":
        return
    if axis == "X":
        circuit.h(qubit)
    elif axis == "Y":
        circuit.sdg(qubit)
        circuit.h(qubit)
    else:  # pragma: no cover
        raise ValueError(f"Unknown axis: {axis}")


def build_circuit(
    spec: CircuitSpec, *, optimized_mid_circuit_measurement: bool = False
) -> QuantumCircuit:
    """Build one direct, dynamic, or uncorrected teleportation circuit.

    All modes use identical register structure so result parsing stays stable.
    The optional IBM-specific measurement instruction is imported lazily, which
    keeps the scientific circuit module usable with local Qiskit/Aer alone.
    """

    q = QuantumRegister(3, "q")
    m0 = ClassicalRegister(1, "m0")
    m1 = ClassicalRegister(1, "m1")
    out = ClassicalRegister(1, "out")
    circuit = QuantumCircuit(q, m0, m1, out, name=spec.name)
    circuit.metadata = {
        "mode": spec.mode,
        "state": spec.state,
        "axis": spec.axis,
        "eigenvalue": spec.eigenvalue,
    }

    if spec.mode == "direct":
        _prepare_state(circuit, q[2], spec.state)
    else:
        _prepare_state(circuit, q[0], spec.state)

        # Bell pair on q1-q2, followed by a Bell-basis measurement of q0-q1.
        circuit.h(q[1])
        circuit.cx(q[1], q[2])
        circuit.cx(q[0], q[1])
        circuit.h(q[0])

        if optimized_mid_circuit_measurement:
            from qiskit_ibm_runtime.circuit import MidCircuitMeasure

            circuit.append(MidCircuitMeasure(), [q[0]], [m0[0]])
            circuit.append(MidCircuitMeasure(), [q[1]], [m1[0]])
        else:
            circuit.measure(q[0], m0[0])
            circuit.measure(q[1], m1[0])

        if spec.mode == "dynamic":
            # Standard teleportation correction: X^m1 Z^m0.
            with circuit.if_test((m1, 1)):
                circuit.x(q[2])
            with circuit.if_test((m0, 1)):
                circuit.z(q[2])

    _rotate_for_measurement(circuit, q[2], spec.axis)
    circuit.measure(q[2], out[0])
    return circuit


def build_experiment(
    *, optimized_mid_circuit_measurement: bool = False
) -> tuple[list[CircuitSpec], list[QuantumCircuit]]:
    """Build the complete frozen experiment."""

    specs = experiment_specs()
    circuits = [
        build_circuit(
            spec,
            optimized_mid_circuit_measurement=optimized_mid_circuit_measurement,
        )
        for spec in specs
    ]
    return specs, circuits
