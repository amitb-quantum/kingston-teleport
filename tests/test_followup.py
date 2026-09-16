import pytest

from kingston_live_teleportation.followup import (
    FOLLOWUP_MODES,
    FollowupSpec,
    analyze_followup_counts,
    build_followup_experiment,
    followup_offline_flip,
    followup_specs,
)


def test_followup_design_is_frozen_and_deterministic():
    specs, circuits = build_followup_experiment()
    assert len(specs) == len(circuits) == 54
    assert tuple(dict.fromkeys(spec.mode for spec in specs)) == FOLLOWUP_MODES
    assert len({spec.name for spec in specs}) == 54
    assert [circuit.name for circuit in circuits] == [spec.name for spec in specs]


def test_both_and_shadow_each_have_two_conditionals():
    specs, circuits = build_followup_experiment()
    by_mode = {
        spec.mode: circuit
        for spec, circuit in zip(specs, circuits, strict=True)
        if spec.state == "0"
    }
    assert by_mode["both"].count_ops()["if_else"] == 2
    assert by_mode["conditional_shadow"].count_ops()["if_else"] == 2


@pytest.mark.parametrize(
    ("mode", "axis", "m0", "m1", "expected"),
    [
        ("none", "X", 1, 0, 1),
        ("none", "Z", 0, 1, 1),
        ("none", "Y", 1, 1, 0),
        ("x_only", "X", 1, 0, 1),
        ("x_only", "Z", 0, 1, 0),
        ("z_only", "X", 1, 0, 0),
        ("z_only", "Z", 0, 1, 1),
        ("both", "Y", 1, 0, 0),
    ],
)
def test_missing_correction_rules(mode, axis, m0, m1, expected):
    spec = FollowupSpec(mode, "0", axis, 1)
    assert followup_offline_flip(spec, m0, m1) == expected


def test_followup_bootstrap_is_deterministic_and_requires_complete_data():
    counts = {spec.name: {"000": 64} for spec in followup_specs()}
    first = analyze_followup_counts(counts, bootstrap_draws=100)
    second = analyze_followup_counts(counts, bootstrap_draws=100)
    assert first == second
    counts.pop(next(iter(counts)))
    with pytest.raises(ValueError, match="missing"):
        analyze_followup_counts(counts, bootstrap_draws=10)


def test_followup_rejects_malformed_counts():
    counts = {spec.name: {"000": 64} for spec in followup_specs()}
    counts[followup_specs()[0].name] = {"000": -1}
    with pytest.raises(ValueError, match="Invalid count"):
        analyze_followup_counts(counts, bootstrap_draws=10)
