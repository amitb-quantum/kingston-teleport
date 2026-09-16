from kingston_live_teleportation.analysis import score_counts, wilson_interval
from kingston_live_teleportation.circuits import CircuitSpec


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
