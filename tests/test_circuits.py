from kingston_live_teleportation.circuits import build_experiment, experiment_specs


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
