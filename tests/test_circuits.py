import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.providers.fake_provider import GenericBackendV2
from qiskit.quantum_info import Statevector
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

from kingston_live_teleportation.circuits import (
    STATE_SPECS,
    _prepare_state,
    _rotate_for_measurement,
    build_experiment,
    experiment_specs,
)


def test_frozen_design_has_eighteen_physical_circuits():
    specs = experiment_specs()
    assert len(specs) == 18
    assert {spec.mode for spec in specs} == {"direct", "dynamic", "uncorrected"}


def test_register_contract_and_dynamic_control_flow():
    specs, circuits = build_experiment()
    assert len(circuits) == len(specs)
    for circuit in circuits:
        assert [register.name for register in circuit.cregs] == ["m0", "m1", "out"]

    dynamic = next(
        circuit
        for spec, circuit in zip(specs, circuits, strict=True)
        if spec.mode == "dynamic"
    )
    assert dynamic.count_ops()["if_else"] == 2


@pytest.mark.parametrize("state,axis,eigenvalue", STATE_SPECS)
def test_all_six_state_preparations_and_measurement_bases(state, axis, eigenvalue):
    circuit = QuantumCircuit(1)
    _prepare_state(circuit, circuit.qubits[0], state)
    _rotate_for_measurement(circuit, circuit.qubits[0], axis)
    probabilities = Statevector.from_instruction(circuit).probabilities()
    desired = 0 if eigenvalue == 1 else 1
    assert probabilities[desired] == pytest.approx(1.0)


def test_spec_names_and_order_are_deterministic():
    assert experiment_specs() == experiment_specs()
    assert [spec.name for spec in experiment_specs()] == [
        f"{mode}__{slug}__{axis}"
        for mode in ("direct", "dynamic", "uncorrected")
        for slug, axis in (
            ("0", "z"), ("1", "z"), ("plus", "x"), ("minus", "x"),
            ("plusi", "y"), ("minusi", "y")
        )
    ]


def test_requested_layout_is_preserved_by_transpilation():
    backend = GenericBackendV2(
        num_qubits=5,
        coupling_map=[
            (0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2), (3, 4), (4, 3)
        ],
        control_flow=True,
        seed=91,
    )
    _, circuits = build_experiment()
    manager = generate_preset_pass_manager(
        backend=backend, optimization_level=1, initial_layout=[1, 2, 3]
    )
    isa = manager.run(circuits)
    for circuit in isa:
        assert circuit.layout.initial_index_layout(filter_ancillas=True) == [1, 2, 3]
        assert circuit.layout.final_index_layout(filter_ancillas=True) == [1, 2, 3]
