from pathlib import Path

import pytest
from qiskit_ibm_runtime.options import SamplerOptions

from kingston_live_teleportation.circuits import experiment_specs
from kingston_live_teleportation.followup_workflow import (
    ibm_quick_usage_estimate,
    run_followup_hardware,
    validate_followup_budget,
)
from kingston_live_teleportation.followup_reporting import summarize_scheduler_timing
from kingston_live_teleportation.verification import (
    verify_followup_results,
    verify_results,
)
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


def test_followup_bundle_verifies_fully_offline():
    result = verify_followup_results(Path(__file__).parents[1])
    assert result["status"] == "verified"
    assert result["job_id"] == "dal13ss62pvc739q7gig"
    assert result["qpu_seconds"] == 10
    assert result["circuits"] == result["scheduler_timing_records"] == 54


def test_scheduler_summary_recovers_exact_branch_and_delay_increments():
    root = Path(__file__).parents[1]
    run_dir = (
        root
        / "followups"
        / "klt-002-delay-corrections"
        / "artifacts"
        / "hardware_20260916T035011Z"
    )
    import json

    metadata = json.loads(
        (run_dir / "scheduler_timing_metadata.json").read_text(encoding="utf-8")
    )
    rows = summarize_scheduler_timing(
        metadata, dt_seconds=4e-9, output_qubit=149
    )
    by_mode = {row["mode"]: row for row in rows}
    assert by_mode["x_only"]["increment_vs_none_us"] == pytest.approx(0.536)
    assert by_mode["z_only"]["increment_vs_none_us"] == pytest.approx(0.548)
    assert by_mode["both"]["increment_vs_none_us"] == pytest.approx(0.768)
    assert by_mode["conditional_shadow"]["increment_vs_none_us"] == pytest.approx(
        0.624
    )
    for delay in (2, 4, 8):
        assert by_mode[f"delay_{delay}us"]["increment_vs_none_us"] == pytest.approx(
            float(delay)
        )


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
