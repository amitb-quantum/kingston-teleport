"""Tabular and graphical output."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def write_tables(run_dir: Path, analysis: dict) -> None:
    per_state = analysis["per_state"]
    with (run_dir / "per_state.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(per_state[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(per_state)

    summary = analysis["summary"]
    with (run_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(summary[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(summary)

    axis_summary = analysis["axis_summary"]
    with (run_dir / "axis_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(axis_summary[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(axis_summary)


def plot_summary(run_dir: Path, analysis: dict) -> Path:
    order = ["direct", "dynamic", "offline", "uncorrected"]
    by_name = {row["estimator"]: row for row in analysis["summary"]}
    means = [by_name[name]["mean_fidelity"] for name in order]
    lows = [means[i] - by_name[name]["ci95_low"] for i, name in enumerate(order)]
    highs = [by_name[name]["ci95_high"] - means[i] for i, name in enumerate(order)]

    fig, ax = plt.subplots(figsize=(8.2, 4.8), constrained_layout=True)
    colors = ["#496F9E", "#D55E00", "#009E73", "#777777"]
    ax.bar(order, means, color=colors, alpha=0.9)
    ax.errorbar(
        range(len(order)),
        means,
        yerr=[lows, highs],
        fmt="none",
        ecolor="black",
        capsize=5,
        linewidth=1.2,
    )
    ax.axhline(2.0 / 3.0, color="#AA3377", linestyle="--", linewidth=1.4)
    ax.text(3.48, 2.0 / 3.0 + 0.015, "classical limit 2/3", ha="right", color="#AA3377")
    ax.set_ylim(0.0, 1.03)
    ax.set_ylabel("Six-state mean fidelity")
    ax.set_title("IBM Kingston dynamic teleportation")
    ax.grid(axis="y", alpha=0.2)
    for index, value in enumerate(means):
        ax.text(index, value + 0.035, f"{value:.3f}", ha="center", fontsize=9)

    path = run_dir / "fidelity_comparison.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def plot_axis_summary(run_dir: Path, analysis: dict) -> Path:
    axes = ["X", "Y", "Z"]
    by_key = {
        (row["estimator"], row["axis"]): row
        for row in analysis["axis_summary"]
    }
    width = 0.34
    positions = list(range(len(axes)))

    fig, ax = plt.subplots(figsize=(7.4, 4.6), constrained_layout=True)
    for offset, estimator, color in (
        (-width / 2, "dynamic", "#D55E00"),
        (+width / 2, "offline", "#009E73"),
    ):
        values = [by_key[(estimator, axis)]["mean_fidelity"] for axis in axes]
        ax.bar(
            [position + offset for position in positions],
            values,
            width=width,
            label=estimator,
            color=color,
        )
    ax.axhline(2.0 / 3.0, color="#AA3377", linestyle="--", linewidth=1.3)
    ax.set_xticks(positions, axes)
    ax.set_ylim(0.0, 1.03)
    ax.set_xlabel("Input/measurement Pauli axis")
    ax.set_ylabel("Mean fidelity of eigenstate pair")
    ax.set_title("Axis-resolved teleportation fidelity")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.2)
    path = run_dir / "axis_fidelity.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path
