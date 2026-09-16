"""Pure, deterministic selection rule for a future spatial replication."""

from __future__ import annotations

from dataclasses import dataclass

REPLICATION_IDLE_SECONDS = 8e-6


@dataclass(frozen=True)
class ChainCalibration:
    """Frozen calibration inputs for one directed input-ancilla-output chain."""

    qubits: tuple[int, int, int]
    cz_errors: tuple[float, float]
    readout_errors: tuple[float, float, float]
    t2_seconds: tuple[float, float, float]

    @property
    def score(self) -> float:
        if any(value < 0 for value in self.cz_errors + self.readout_errors):
            raise ValueError("Error probabilities must be non-negative")
        if any(value <= 0 for value in self.t2_seconds):
            raise ValueError("T2 values must be positive")
        return (
            sum(self.cz_errors)
            + sum(self.readout_errors)
            + sum(REPLICATION_IDLE_SECONDS / value for value in self.t2_seconds)
        )


def select_replication_chain(
    candidates: list[ChainCalibration],
    *,
    excluded_qubits: frozenset[int] = frozenset({147, 148, 149}),
) -> ChainCalibration:
    """Choose the minimum frozen score, breaking exact ties lexicographically."""

    eligible = [
        candidate
        for candidate in candidates
        if not (set(candidate.qubits) & excluded_qubits)
        and len(set(candidate.qubits)) == 3
    ]
    if not eligible:
        raise ValueError("No eligible non-overlapping three-qubit chain")
    return min(eligible, key=lambda item: (item.score, item.qubits))
