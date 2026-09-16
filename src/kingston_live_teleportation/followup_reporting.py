"""Deterministic derived tables and figures for the completed KLT-002 job."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .followup import FOLLOWUP_MODES
from .followup_workflow import write_sha256_manifest
from .io import read_json, write_json


def _scheduler_events(timing: str) -> list[dict]:
    events = []
    for row in csv.reader(io.StringIO(timing)):
        if not row:
            continue
        if len(row) != 6:
            raise ValueError(f"Malformed scheduler row: {row!r}")
        events.append(
            {
                "branch": row[0],
                "instruction": row[1],
                "channel": row[2],
                "t0_dt": int(row[3]),
                "duration_dt": int(row[4]),
                "event": row[5],
            }
        )
    return events


def summarize_scheduler_timing(
    metadata: list[dict],
    *,
    dt_seconds: float,
    output_qubit: int,
    state: str = "+",
) -> list[dict]:
    """Summarize one state across modes using archived accurate schedules."""

    by_mode = {
        record["circuit_metadata"]["mode"]: record
        for record in metadata
        if record["circuit_metadata"]["state"] == state
    }
    if set(by_mode) != set(FOLLOWUP_MODES):
        raise ValueError("Scheduler metadata does not contain every KLT-002 mode")

    rows = []
    for mode in FOLLOWUP_MODES:
        record = by_mode[mode]
        scheduler = record["compilation"]["scheduler_timing"]
        events = _scheduler_events(scheduler["timing"])
        captures = [
            event["t0_dt"] + event["duration_dt"]
            for event in events
            if event["instruction"].startswith("measure_2_")
            and event["event"] == "capture"
        ]
        output_starts = [
            event["t0_dt"]
            for event in events
            if event["instruction"] == f"measure_{output_qubit}"
            and event["channel"] == f"Qubit {output_qubit}"
            and event["event"] == "play"
        ]
        if not output_starts:
            raise ValueError(f"No output measurement start found for {mode}")
        capture_end = max(captures) if captures else None
        output_start = min(output_starts)
        rows.append(
            {
                "mode": mode,
                "state": state,
                "axis": record["circuit_metadata"]["axis"],
                "circuit_duration_dt": scheduler["circuit_duration"],
                "circuit_duration_us": round(
                    scheduler["circuit_duration"] * dt_seconds * 1e6, 6
                ),
                "mcm_capture_end_dt": capture_end,
                "output_measure_start_dt": output_start,
                "wait_after_mcm_us": None
                if capture_end is None
                else round((output_start - capture_end) * dt_seconds * 1e6, 6),
            }
        )

    baseline = next(row for row in rows if row["mode"] == "none")
    for row in rows:
        row["increment_vs_none_us"] = (
            None
            if row["wait_after_mcm_us"] is None
            else round(
                row["wait_after_mcm_us"] - baseline["wait_after_mcm_us"], 6
            )
        )
    return rows


def _write_timing_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _plot_results(path: Path, analysis: dict) -> None:
    summary = {row["mode"]: row for row in analysis["summary"]}
    axes = ("X", "Y", "Z")
    axis_values = {
        (row["mode"], row["axis"]): row["mean_fidelity"]
        for row in analysis["axis_summary"]
    }
    labels = [mode.replace("_", " ") for mode in FOLLOWUP_MODES]
    means = [summary[mode]["mean_fidelity"] for mode in FOLLOWUP_MODES]
    low = [
        means[index] - summary[mode]["ci95_low"]
        for index, mode in enumerate(FOLLOWUP_MODES)
    ]
    high = [
        summary[mode]["ci95_high"] - means[index]
        for index, mode in enumerate(FOLLOWUP_MODES)
    ]

    fig, (left, right) = plt.subplots(
        1, 2, figsize=(13.0, 6.4), gridspec_kw={"width_ratios": [1.05, 1.0]}
    )
    positions = np.arange(len(FOLLOWUP_MODES))
    colors = [
        "#496F9E",
        "#009E73",
        "#E69F00",
        "#CC79A7",
        "#D55E00",
        "#8C564B",
        "#56B4E9",
        "#7F7F7F",
        "#333333",
    ]
    left.barh(positions, means, color=colors)
    left.errorbar(
        means,
        positions,
        xerr=[low, high],
        fmt="none",
        ecolor="black",
        capsize=3,
        linewidth=1,
    )
    left.axvline(2 / 3, color="#AA3377", linestyle="--", linewidth=1.3)
    left.set_yticks(positions, labels)
    left.invert_yaxis()
    left.set_xlim(0.4, 1.04)
    left.set_xlabel("Six-state mean fidelity")
    left.set_title("Mode means with bootstrap 95% intervals")
    left.grid(axis="x", alpha=0.2)
    for position, value in zip(positions, means, strict=True):
        if value > 0.95:
            left.text(
                value - 0.02,
                position,
                f"{value:.3f}",
                ha="right",
                va="center",
                color="white",
                fontsize=8,
            )
        else:
            left.text(
                value + 0.008, position, f"{value:.3f}", va="center", fontsize=8
            )

    matrix = np.array(
        [[axis_values[(mode, axis)] for axis in axes] for mode in FOLLOWUP_MODES]
    )
    image = right.imshow(matrix, vmin=0.3, vmax=1.0, cmap="viridis", aspect="auto")
    right.set_xticks(range(3), axes)
    right.set_yticks(range(len(labels)), labels)
    right.set_xlabel("Input and measurement axis")
    right.set_title("Axis-resolved fidelity")
    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = matrix[row_index, column_index]
            color = "white" if value < 0.62 else "black"
            right.text(
                column_index,
                row_index,
                f"{value:.3f}",
                ha="center",
                va="center",
                color=color,
                fontsize=8,
            )
    fig.colorbar(image, ax=right, fraction=0.046, pad=0.04, label="Fidelity")
    fig.suptitle("KLT-002: correction-path and latency-bracketing controls")
    fig.tight_layout()
    fig.savefig(path, dpi=180, metadata={"Software": "kingston-live-teleportation"})
    plt.close(fig)


def build_followup_report_artifacts(run_dir: Path, output_dir: Path) -> Path:
    """Create reproducible derived timing, decision, and figure artifacts."""

    output_dir.mkdir(parents=True, exist_ok=True)
    analysis = read_json(run_dir / "analysis.json")
    preflight = read_json(run_dir / "preflight.json")
    metadata = read_json(run_dir / "scheduler_timing_metadata.json")
    timing_rows = summarize_scheduler_timing(
        metadata,
        dt_seconds=preflight["dt_seconds"],
        output_qubit=preflight["physical_qubits"][2],
    )
    _write_timing_csv(output_dir / "scheduler_summary_plus_state.csv", timing_rows)
    _plot_results(output_dir / "klt002_result_summary.png", analysis)

    summary = {row["mode"]: row for row in analysis["summary"]}
    axes = {
        (row["mode"], row["axis"]): row["mean_fidelity"]
        for row in analysis["axis_summary"]
    }
    shadow_differences = {
        axis: abs(axes[("conditional_shadow", axis)] - axes[("both", axis)])
        for axis in ("X", "Y", "Z")
    }
    delay_max_differences = {
        mode: max(
            abs(axes[(mode, axis)] - axes[("both", axis)])
            for axis in ("X", "Y", "Z")
        )
        for mode in ("delay_2us", "delay_4us", "delay_8us")
    }
    manifest = read_json(run_dir / "result_manifest.json")
    write_json(
        output_dir / "derived_manifest.json",
        {
            "source_job_id": manifest["job_id"],
            "source_run_directory": run_dir.name,
            "dt_seconds": preflight["dt_seconds"],
            "timing_state": "+",
            "timing_method": (
                "Archived IBM scheduler events; wait is output-measurement start "
                "minus latest optimized mid-circuit capture end."
            ),
            "preregistered_decisions": {
                "H1_x_only_retains_x_more_than_z_only": (
                    axes[("x_only", "X")] > axes[("z_only", "X")]
                ),
                "H2_both_below_both_single_correction_means": (
                    summary["both"]["mean_fidelity"]
                    < min(
                        summary["x_only"]["mean_fidelity"],
                        summary["z_only"]["mean_fidelity"],
                    )
                ),
                "H3_any_delay_within_0.03_on_every_axis": any(
                    difference <= 0.03
                    for difference in delay_max_differences.values()
                ),
                "H4_shadow_within_0.03_on_every_axis": max(
                    shadow_differences.values()
                )
                <= 0.03,
            },
            "shadow_absolute_axis_differences_from_both": shadow_differences,
            "delay_maximum_absolute_axis_difference_from_both": (
                delay_max_differences
            ),
        },
    )
    write_sha256_manifest(output_dir)
    return output_dir
