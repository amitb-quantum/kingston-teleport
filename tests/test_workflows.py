from pathlib import Path

import pytest
from qiskit_ibm_runtime.options import SamplerOptions

from kingston_live_teleportation.circuits import experiment_specs
from kingston_live_teleportation.followup_workflow import (
    ibm_quick_usage_estimate,
    run_followup_hardware,
    validate_followup_budget,
)
from kingston_live_teleportation.verification import verify_results
from kingston_live_teleportation.workflow import _extract_counts
from kingston_live_teleportation.replication import (
    ChainCalibration,
    select_replication_chain,
)


class FakeJoint:
    def get_counts(self):
        return {"0 0 0": 3, "1 0 1": 2}


class FakePub:
    def __init__(self):
        self.requested_registers = None

    def join_data(self, registers):
        self.requested_registers = registers
        return FakeJoint()


def test_primitive_result_extraction_uses_explicit_register_order():
    spec = experiment_specs()[0]
    pub = FakePub()
    assert _extract_counts([spec], [pub]) == {
        spec.name: {"0 0 0": 3, "1 0 1": 2}
    }
    assert pub.requested_registers == ["m0", "m1", "out"]


def test_primitive_result_extraction_rejects_wrong_length():
    with pytest.raises(ValueError, match="Expected 1 results"):
        _extract_counts([experiment_specs()[0]], [])


def test_followup_budget_formula_caps_and_shots():
    assert ibm_quick_usage_estimate(54, 512) == pytest.approx(11.6768)
    assert validate_followup_budget(512, 20)["executions"] == 27_648
    with pytest.raises(ValueError, match="shots"):
        validate_followup_budget(513, 20)
    with pytest.raises(ValueError, match="must be in"):
        validate_followup_budget(512, 21)
    with pytest.raises(ValueError, match="exceeds cap"):
        validate_followup_budget(512, 10)


def test_followup_requires_literal_submit_before_service_access(tmp_path):
    with pytest.raises(RuntimeError, match="literal --submit"):
        run_followup_hardware(
            tmp_path,
            backend_name="not_contacted",
            instance=None,
            physical_qubits=[0, 1, 2],
            shots=512,
            max_qpu_seconds=20,
            submit=False,
        )


def test_scheduler_timing_option_shape_is_accepted_by_runtime_client():
    options = SamplerOptions()
    options.experimental = {"execution": {"scheduler_timing": True}}
    assert options.experimental == {"execution": {"scheduler_timing": True}}


def test_published_bundle_verifies_fully_offline():
    result = verify_results(Path(__file__).parents[1])
    assert result["status"] == "verified"
    assert result["total_qpu_seconds"] == 13
    assert result["analysis_exactly_regenerated"] is True


def test_replication_chain_selection_is_scored_excluded_and_deterministic():
    shared = dict(
        cz_errors=(0.01, 0.01),
        readout_errors=(0.01, 0.01, 0.01),
        t2_seconds=(100e-6, 100e-6, 100e-6),
    )
    candidates = [
        ChainCalibration((147, 30, 31), **shared),  # excluded despite best tie
        ChainCalibration((9, 8, 7), **shared),
        ChainCalibration((1, 2, 3), **shared),
    ]
    assert select_replication_chain(candidates).qubits == (1, 2, 3)
