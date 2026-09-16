"""Simulation, read-only preflight, and gated execution for KLT-002."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from qiskit import qpy
from qiskit.primitives import BackendSamplerV2
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_aer import AerSimulator
from qiskit_ibm_runtime import SamplerV2

from .followup import analyze_followup_counts, build_followup_experiment
from .io import utc_stamp, write_json
from .protocols import FOLLOWUP_PROTOCOL_ID, FOLLOWUP_PROTOCOL_VERSION
from .report import write_tables
from .workflow import (
    _extract_counts,
    _qargs_supported,
    _service,
    software_manifest,
)

FOLLOWUP_SHOTS = 512
FOLLOWUP_CIRCUITS = 54
FOLLOWUP_HARD_MAX_QPU_SECONDS = 20
IBM_JOB_OVERHEAD_SECONDS = 2.0
IBM_SECONDS_PER_EXECUTION = 0.00035


def ibm_quick_usage_estimate(circuits: int, shots: int) -> float:
    """IBM quick estimate without mitigation or a custom repetition delay."""

    if circuits <= 0 or shots <= 0:
        raise ValueError("circuits and shots must be positive")
    return IBM_JOB_OVERHEAD_SECONDS + IBM_SECONDS_PER_EXECUTION * circuits * shots


def validate_followup_budget(shots: int, max_qpu_seconds: int) -> dict[str, Any]:
    if shots <= 0 or shots > FOLLOWUP_SHOTS:
        raise ValueError(f"KLT-002 shots must be in [1, {FOLLOWUP_SHOTS}]")
    if max_qpu_seconds <= 0 or max_qpu_seconds > FOLLOWUP_HARD_MAX_QPU_SECONDS:
        raise ValueError(
            f"KLT-002 max_qpu_seconds must be in [1, "
            f"{FOLLOWUP_HARD_MAX_QPU_SECONDS}]"
        )
    estimate = ibm_quick_usage_estimate(FOLLOWUP_CIRCUITS, shots)
    if estimate > max_qpu_seconds:
        raise ValueError(
            f"IBM quick estimate {estimate:.4f}s exceeds cap {max_qpu_seconds}s"
        )
    return {
        "formula": "2 + 0.00035 * circuits * shots",
        "assumptions": "no mitigation and no custom repetition delay",
        "circuit_count": FOLLOWUP_CIRCUITS,
        "shots": shots,
        "executions": FOLLOWUP_CIRCUITS * shots,
        "estimated_qpu_seconds": estimate,
        "server_side_cap_seconds": max_qpu_seconds,
    }


def _save_followup(run_dir: Path, counts: dict[str, dict[str, int]]) -> dict:
    analysis = analyze_followup_counts(counts)
    write_json(run_dir / "counts.json", counts)
    write_json(run_dir / "analysis.json", analysis)
    write_tables(run_dir, analysis)
    return analysis


def write_sha256_manifest(run_dir: Path) -> Path:
    """Cover every artifact in a run directory except the manifest itself."""

    manifest = run_dir / "SHA256SUMS"
    rows = []
    for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
        if path == manifest:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(run_dir).as_posix()}")
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return manifest


def simulate_followup(output_root: Path, shots: int, seed: int) -> Path:
    if shots <= 0 or shots > 4096:
        raise ValueError("simulation shots must be in [1, 4096]")
    run_dir = output_root / f"simulation_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    specs, circuits = build_followup_experiment()
    backend = AerSimulator(seed_simulator=seed)
    result = BackendSamplerV2(backend=backend).run(circuits, shots=shots).result()
    counts = _extract_counts(specs, result)
    analysis = _save_followup(run_dir, counts)
    write_json(
        run_dir / "manifest.json",
        {
            "kind": "KLT-002 ideal simulation",
            "protocol_id": FOLLOWUP_PROTOCOL_ID,
            "protocol_version": FOLLOWUP_PROTOCOL_VERSION,
            "shots": shots,
            "seed": seed,
            "circuits": [asdict(spec) | {"name": spec.name} for spec in specs],
            "summary": analysis["summary"],
            "software": software_manifest(),
            "qpu_submission_performed": False,
        },
    )
    write_sha256_manifest(run_dir)
    return run_dir


def _layout_indices(circuit) -> tuple[list[int], list[int]]:
    if circuit.layout is None:
        raise RuntimeError(f"Transpiled circuit {circuit.name} has no layout")
    initial = circuit.layout.initial_index_layout(filter_ancillas=True)
    final = circuit.layout.final_index_layout(filter_ancillas=True)
    return list(initial), list(final)


def _duration(target, operation: str, qargs: tuple[int, ...]) -> float | None:
    try:
        props = target[operation]
    except KeyError:
        return None
    prop = props.get(qargs, props.get(None))
    value = getattr(prop, "duration", None)
    return None if value is None else float(value)


def _live_followup_preflight(
    *,
    backend_name: str,
    instance: str | None,
    physical_qubits: list[int],
    shots: int,
    max_qpu_seconds: int,
):
    budget = validate_followup_budget(shots, max_qpu_seconds)
    if len(physical_qubits) != 3 or len(set(physical_qubits)) != 3:
        raise ValueError("Exactly three distinct physical qubits are required")

    service = _service(instance)
    backend = service.backend(backend_name)
    status = backend.status()
    if not status.operational:
        raise RuntimeError(f"Backend {backend_name} is not operational")
    required = {"if_else", "measure_2", "cz", "delay"}
    missing = required - set(backend.target.operation_names)
    if missing:
        raise RuntimeError(f"Backend lacks required operations: {sorted(missing)}")
    for qubit in physical_qubits[:2]:
        if not _qargs_supported(backend.target, "measure_2", (qubit,)):
            raise RuntimeError(f"measure_2 unavailable on physical qubit {qubit}")
    for left, right in zip(physical_qubits, physical_qubits[1:]):
        if not (
            _qargs_supported(backend.target, "cz", (left, right))
            or _qargs_supported(backend.target, "cz", (right, left))
        ):
            raise RuntimeError(f"No native CZ edge between {left} and {right}")

    specs, circuits = build_followup_experiment(
        optimized_mid_circuit_measurement=True
    )
    pass_manager = generate_preset_pass_manager(
        backend=backend, optimization_level=1, initial_layout=physical_qubits
    )
    isa_circuits = pass_manager.run(circuits)
    metrics = []
    expected_layout = list(physical_qubits)
    for spec, circuit in zip(specs, isa_circuits, strict=True):
        initial, final = _layout_indices(circuit)
        if initial != expected_layout or final != expected_layout:
            raise RuntimeError(
                f"Layout drift in {spec.name}: initial={initial}, final={final}"
            )
        delay_durations = [
            {
                "duration": instruction.operation.duration,
                "unit": instruction.operation.unit,
                "physical_qubit": circuit.find_bit(instruction.qubits[0]).index,
            }
            for instruction in circuit.data
            if instruction.operation.name == "delay"
        ]
        if spec.delay_us and not delay_durations:
            raise RuntimeError(f"Preregistered delay disappeared in {spec.name}")
        if spec.delay_us:
            expected_dt = round(spec.delay_us * 1e-6 / backend.dt)
            expected = [{
                "duration": expected_dt,
                "unit": "dt",
                "physical_qubit": physical_qubits[2],
            }]
            if delay_durations != expected:
                raise RuntimeError(
                    f"Delay drift in {spec.name}: expected={expected}, "
                    f"observed={delay_durations}"
                )
        metrics.append(
            {
                "name": spec.name,
                "mode": spec.mode,
                "depth": circuit.depth(),
                "size": circuit.size(),
                "operations": dict(circuit.count_ops()),
                "initial_layout": initial,
                "final_layout": final,
                "delays": delay_durations,
            }
        )

    properties = backend.properties()
    payload = {
        "kind": "KLT-002 live preflight; no submission",
        "protocol_id": FOLLOWUP_PROTOCOL_ID,
        "protocol_version": FOLLOWUP_PROTOCOL_VERSION,
        "backend": backend_name,
        "backend_version": backend.backend_version,
        "instance": instance,
        "operational": status.operational,
        "pending_jobs": status.pending_jobs,
        "physical_qubits": physical_qubits,
        "dt_seconds": backend.dt,
        "calibration_last_update": getattr(properties, "last_update_date", None),
        "static_instruction_durations_seconds": {
            "x_output": _duration(backend.target, "x", (physical_qubits[2],)),
            "sx_output": _duration(backend.target, "sx", (physical_qubits[2],)),
            "cz_input_ancilla": _duration(
                backend.target, "cz", tuple(physical_qubits[:2])
            ),
            "cz_ancilla_output": _duration(
                backend.target, "cz", tuple(physical_qubits[1:])
            ),
            "measure_2_input": _duration(
                backend.target, "measure_2", (physical_qubits[0],)
            ),
            "terminal_measure_output": _duration(
                backend.target, "measure", (physical_qubits[2],)
            ),
            "if_else": _duration(backend.target, "if_else", ()),
        },
        "selected_qubit_calibration": {
            str(qubit): {
                "t1_seconds": properties.t1(qubit),
                "t2_seconds": properties.t2(qubit),
                "readout_error": properties.readout_error(qubit),
            }
            for qubit in physical_qubits
        },
        "selected_edge_calibration": {
            f"{left}-{right}": {
                "cz_error": properties.gate_error("cz", [left, right]),
                "cz_length_seconds": properties.gate_length("cz", [left, right]),
            }
            for left, right in zip(physical_qubits, physical_qubits[1:])
        },
        "timing_conclusion": (
            "The target does not expose a complete if_else/feed-forward duration. "
            "The 0/2/4/8 us controls bracket, but do not claim to match, latency."
        ),
        "future_result_requirement": (
            "Request experimental scheduler_timing metadata in any authorized "
            "hardware job and archive result metadata."
        ),
        "budget": budget,
        "circuit_metrics": metrics,
        "software": software_manifest(),
        "submission_performed": False,
    }
    return backend, specs, isa_circuits, payload


def preflight_followup(
    output_root: Path,
    *,
    backend_name: str,
    instance: str | None,
    physical_qubits: list[int],
    shots: int,
    max_qpu_seconds: int,
) -> Path:
    _, _, isa_circuits, payload = _live_followup_preflight(
        backend_name=backend_name,
        instance=instance,
        physical_qubits=physical_qubits,
        shots=shots,
        max_qpu_seconds=max_qpu_seconds,
    )
    run_dir = output_root / f"preflight_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "preflight.json", payload)
    with (run_dir / "isa_circuits.qpy").open("wb") as handle:
        qpy.dump(isa_circuits, handle)
    write_sha256_manifest(run_dir)
    return run_dir


def run_followup_hardware(
    output_root: Path,
    *,
    backend_name: str,
    instance: str | None,
    physical_qubits: list[int],
    shots: int,
    max_qpu_seconds: int,
    submit: bool,
) -> tuple[Path, str]:
    if not submit:
        raise RuntimeError("KLT-002 hardware submission requires literal --submit")
    backend, specs, circuits, preflight = _live_followup_preflight(
        backend_name=backend_name,
        instance=instance,
        physical_qubits=physical_qubits,
        shots=shots,
        max_qpu_seconds=max_qpu_seconds,
    )
    run_dir = output_root / f"hardware_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "preflight.json", preflight)
    with (run_dir / "isa_circuits.qpy").open("wb") as handle:
        qpy.dump(circuits, handle)

    sampler = SamplerV2(mode=backend)
    sampler.options.max_execution_time = max_qpu_seconds
    sampler.options.environment.job_tags = [
        "kingston-live-teleportation",
        FOLLOWUP_PROTOCOL_ID,
        f"v{FOLLOWUP_PROTOCOL_VERSION}",
    ]
    sampler.options.experimental = {"execution": {"scheduler_timing": True}}
    job = sampler.run(circuits, shots=shots)
    submission = {
        "job_id": job.job_id(),
        "backend": backend_name,
        "instance": instance,
        "physical_qubits": physical_qubits,
        "shots": shots,
        "max_qpu_seconds": max_qpu_seconds,
        "submitted_at": utc_stamp(),
        "scheduler_timing_requested": True,
    }
    write_json(run_dir / "submission.json", submission)
    result = job.result()
    analysis = _save_followup(run_dir, _extract_counts(specs, result))
    metrics = job.metrics()
    write_json(run_dir / "job_metrics.json", metrics)
    write_json(
        run_dir / "scheduler_timing_metadata.json",
        [getattr(pub, "metadata", {}) for pub in result],
    )
    write_json(
        run_dir / "result_manifest.json",
        {
            **submission,
            "completed_at": utc_stamp(),
            "final_status": str(job.status()),
            "ibm_quantum_seconds": metrics.get("usage", {}).get("quantum_seconds"),
            "summary": analysis["summary"],
        },
    )
    write_sha256_manifest(run_dir)
    return run_dir, job.job_id()
