"""Exploratory register-by-correction feed-forward diagnostic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit_ibm_runtime.circuit import MidCircuitMeasure


@dataclass(frozen=True)
class DiagnosticSpec:
    source: Literal["m0", "m1"]
    correction: Literal["X", "Z"]

    @property
    def name(self) -> str:
        return f"diagnostic__{self.source}_to_{self.correction.lower()}"


def build_diagnostics() -> tuple[list[DiagnosticSpec], list[QuantumCircuit]]:
    specs = [
        DiagnosticSpec(source, correction)
        for source in ("m0", "m1")
        for correction in ("X", "Z")
    ]
    circuits: list[QuantumCircuit] = []
    for spec in specs:
        q = QuantumRegister(3, "q")
        m0 = ClassicalRegister(1, "m0")
        m1 = ClassicalRegister(1, "m1")
        out = ClassicalRegister(1, "out")
        circuit = QuantumCircuit(q, m0, m1, out, name=spec.name)
        circuit.h(q[0])
        circuit.h(q[1])
        if spec.correction == "Z":
            circuit.h(q[2])
        circuit.append(MidCircuitMeasure(), [q[0]], [m0[0]])
        circuit.append(MidCircuitMeasure(), [q[1]], [m1[0]])
        source_register = m0 if spec.source == "m0" else m1
        with circuit.if_test((source_register, 1)):
            if spec.correction == "X":
                circuit.x(q[2])
            else:
                circuit.z(q[2])
        if spec.correction == "Z":
            circuit.h(q[2])
        circuit.measure(q[2], out[0])
        circuits.append(circuit)
    return specs, circuits


@dataclass(frozen=True)
class SequenceDiagnosticSpec:
    order: Literal["XZ", "ZX"]
    basis: Literal["X", "Z"]

    @property
    def name(self) -> str:
        return f"sequence__{self.order.lower()}__measure_{self.basis.lower()}"


def build_sequence_diagnostics() -> tuple[
    list[SequenceDiagnosticSpec], list[QuantumCircuit]
]:
    """Test whether both of two sequential conditional corrections survive."""

    specs = [
        SequenceDiagnosticSpec(order, basis)
        for order in ("XZ", "ZX")
        for basis in ("X", "Z")
    ]
    circuits: list[QuantumCircuit] = []
    for spec in specs:
        q = QuantumRegister(3, "q")
        m0 = ClassicalRegister(1, "m0")
        m1 = ClassicalRegister(1, "m1")
        out = ClassicalRegister(1, "out")
        circuit = QuantumCircuit(q, m0, m1, out, name=spec.name)
        circuit.h(q[0])
        circuit.h(q[1])
        if spec.basis == "X":
            circuit.h(q[2])
        circuit.append(MidCircuitMeasure(), [q[0]], [m0[0]])
        circuit.append(MidCircuitMeasure(), [q[1]], [m1[0]])
        for correction in spec.order:
            register = m1 if correction == "X" else m0
            with circuit.if_test((register, 1)):
                if correction == "X":
                    circuit.x(q[2])
                else:
                    circuit.z(q[2])
        if spec.basis == "X":
            circuit.h(q[2])
        circuit.measure(q[2], out[0])
        circuits.append(circuit)
    return specs, circuits
