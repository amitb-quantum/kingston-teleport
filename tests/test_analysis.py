import json
from pathlib import Path

import pytest

from kingston_live_teleportation.analysis import (
    _decode_joint_key,
    _offline_flip,
    analyze_counts,
    score_counts,
    wilson_interval,
)
from kingston_live_teleportation.circuits import CircuitSpec, experiment_specs


def test_wilson_interval_contains_observed_rate():
    low, high = wilson_interval(80, 100)
    assert low < 0.8 < high


def test_offline_z_correction_uses_m1():
    spec = CircuitSpec("uncorrected", "0", "Z", +1)
    counts = {
        "000": 25,
        "001": 25,
        "110": 25,
        "111": 25,
    }
    raw_successes, total = score_counts(spec, counts)
    corrected_successes, _ = score_counts(spec, counts, offline_correction=True)
    assert raw_successes == 50
    assert corrected_successes == total == 100


def test_offline_y_correction_uses_bell_parity():
    spec = CircuitSpec("uncorrected", "+i", "Y", +1)
    counts = {
        "000": 25,
        "101": 25,
        "110": 25,
        "011": 25,
    }
    corrected_successes, total = score_counts(spec, counts, offline_correction=True)
    assert corrected_successes == total == 100


@pytest.mark.parametrize(
    ("axis", "m0", "m1", "expected"),
    [("X", 1, 0, 1), ("Z", 0, 1, 1), ("Y", 1, 0, 1), ("Y", 1, 1, 0)],
)
def test_offline_pauli_rules(axis, m0, m1, expected):
    assert _offline_flip(axis, m0, m1) == expected


def test_qiskit_count_key_is_displayed_out_m1_m0():
    assert _decode_joint_key("101") == (1, 0, 1)
    assert _decode_joint_key("1 0 1") == (1, 0, 1)


def test_published_analysis_regenerates_exactly_and_deterministically():
    root = Path(__file__).parents[1]
    bundle = root / "results" / "kingston-2026-09-15" / "main"
    counts = json.loads((bundle / "counts.json").read_text(encoding="utf-8"))
    published = json.loads((bundle / "analysis.json").read_text(encoding="utf-8"))
    first = analyze_counts(experiment_specs(), counts)
    second = analyze_counts(experiment_specs(), counts)
    assert first == second == published


def test_analysis_rejects_missing_extra_and_malformed_data():
    specs = experiment_specs()
    counts = {spec.name: {"000": 1} for spec in specs}
    counts.pop(specs[0].name)
    with pytest.raises(ValueError, match="missing"):
        analyze_counts(specs, counts)

    counts = {spec.name: {"000": 1} for spec in specs} | {"unexpected": {"000": 1}}
    with pytest.raises(ValueError, match="extra"):
        analyze_counts(specs, counts)

    with pytest.raises(ValueError, match="Invalid count"):
        score_counts(specs[0], {"000": 0})
