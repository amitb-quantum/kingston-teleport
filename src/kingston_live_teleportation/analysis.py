"""Count-level analysis with reproducible uncertainty estimates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from math import sqrt
from typing import Iterable

import numpy as np

from .circuits import CircuitSpec


def wilson_interval(successes: int, total: int, z: float = 1.95996398454) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion."""

    if total <= 0:
        raise ValueError("total must be positive")
    p = successes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denom
    spread = z * sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / denom
    return max(0.0, center - spread), min(1.0, center + spread)


def _decode_joint_key(key: str) -> tuple[int, int, int]:
    """Decode a BitArray key joined in explicit [m0, m1, out] order.

    Qiskit's displayed count keys follow classical bitstring convention: the
    last-added register is printed on the left. ``join_data([m0, m1, out])``
    therefore produces displayed keys in ``out m1 m0`` order.
    """

    bits = key.replace(" ", "").zfill(3)
    if len(bits) != 3 or set(bits) - {"0", "1"}:
        raise ValueError(f"Expected a three-bit binary key, got {key!r}")
    return int(bits[2]), int(bits[1]), int(bits[0])


def _offline_flip(axis: str, m0: int, m1: int) -> int:
    if axis == "Z":
        return m1
    if axis == "X":
        return m0
    if axis == "Y":
        return m0 ^ m1
    raise ValueError(f"Unknown axis: {axis}")


def score_counts(
    spec: CircuitSpec,
    counts: dict[str, int],
    *,
    offline_correction: bool = False,
) -> tuple[int, int]:
    """Return successful and total shots for one circuit."""

    desired = 0 if spec.eigenvalue == 1 else 1
    successes = 0
    total = 0
    for key, count in counts.items():
        m0, m1, outcome = _decode_joint_key(key)
        if offline_correction:
            outcome ^= _offline_flip(spec.axis, m0, m1)
        successes += count if outcome == desired else 0
        total += count
    if total <= 0:
        raise ValueError(f"No shots found for {spec.name}")
    return successes, total


def analyze_counts(
    specs: Iterable[CircuitSpec],
    counts_by_name: dict[str, dict[str, int]],
    *,
    bootstrap_draws: int = 20_000,
    seed: int = 240915,
) -> dict:
    """Analyze physical modes and derive the offline-corrected estimator."""

    rows: list[dict] = []
    for spec in specs:
        counts = counts_by_name[spec.name]
        successes, total = score_counts(spec, counts)
        low, high = wilson_interval(successes, total)
        rows.append(
            {
                **asdict(spec),
                "estimator": spec.mode,
                "circuit": spec.name,
                "successes": successes,
                "shots": total,
                "fidelity": successes / total,
                "ci95_low": low,
                "ci95_high": high,
            }
        )

        if spec.mode == "uncorrected":
            successes, total = score_counts(
                spec, counts, offline_correction=True
            )
            low, high = wilson_interval(successes, total)
            rows.append(
                {
                    **asdict(spec),
                    "estimator": "offline",
                    "circuit": spec.name,
                    "successes": successes,
                    "shots": total,
                    "fidelity": successes / total,
                    "ci95_low": low,
                    "ci95_high": high,
                }
            )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["estimator"]].append(row)

    rng = np.random.default_rng(seed)
    summaries: list[dict] = []
    for estimator in ("direct", "dynamic", "uncorrected", "offline"):
        group = grouped[estimator]
        if len(group) != 6:
            raise ValueError(f"Expected six states for {estimator}, got {len(group)}")
        observed = float(np.mean([row["fidelity"] for row in group]))
        boot = np.zeros(bootstrap_draws, dtype=float)
        for row in group:
            boot += rng.binomial(
                row["shots"], row["fidelity"], size=bootstrap_draws
            ) / row["shots"]
        boot /= len(group)
        low, high = np.quantile(boot, [0.025, 0.975])
        summaries.append(
            {
                "estimator": estimator,
                "mean_fidelity": observed,
                "ci95_low": float(low),
                "ci95_high": float(high),
                "states": len(group),
                "shots_per_state": sorted({row["shots"] for row in group}),
                "above_classical_by_point_estimate": observed > 2.0 / 3.0,
                "ci_excludes_classical_threshold": float(low) > 2.0 / 3.0,
            }
        )

    summary_map = {row["estimator"]: row for row in summaries}
    axis_summary: list[dict] = []
    for estimator in ("direct", "dynamic", "uncorrected", "offline"):
        for axis in ("X", "Y", "Z"):
            axis_rows = [
                row
                for row in grouped[estimator]
                if row["axis"] == axis
            ]
            mean_fidelity = float(
                np.mean([row["fidelity"] for row in axis_rows])
            )
            axis_summary.append(
                {
                    "estimator": estimator,
                    "axis": axis,
                    "mean_fidelity": mean_fidelity,
                    "pauli_transfer_diagonal": 2.0 * mean_fidelity - 1.0,
                    "states": len(axis_rows),
                }
            )
    contrasts = {
        "dynamic_minus_uncorrected": (
            summary_map["dynamic"]["mean_fidelity"]
            - summary_map["uncorrected"]["mean_fidelity"]
        ),
        "dynamic_minus_offline": (
            summary_map["dynamic"]["mean_fidelity"]
            - summary_map["offline"]["mean_fidelity"]
        ),
    }
    return {
        "threshold": {"classical_teleportation_fidelity": 2.0 / 3.0},
        "per_state": rows,
        "summary": summaries,
        "axis_summary": axis_summary,
        "contrasts": contrasts,
        "bootstrap": {"draws": bootstrap_draws, "seed": seed},
    }
