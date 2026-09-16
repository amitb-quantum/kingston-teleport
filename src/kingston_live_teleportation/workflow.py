"""Simulator, live preflight, submission, and recovery workflows."""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

import qiskit
import qiskit_ibm_runtime
from qiskit.exceptions import QiskitError
from qiskit.primitives import BackendSamplerV2
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_aer import AerSimulator
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2

from . import __version__
from .analysis import analyze_counts
from .circuits import CircuitSpec, build_experiment, experiment_specs
from .diagnostics import build_diagnostics, build_sequence_diagnostics
from .io import read_json, utc_stamp, write_json
from .report import plot_axis_summary, plot_summary, write_tables

HARD_MAX_QPU_SECONDS = 25
REGISTER_ORDER = ["m0", "m1", "out"]
CONTROL_FLOW_ALLOWANCE_SECONDS = 500e-6


def software_manifest() -> dict[str, str]:
    import qiskit_aer

    return {
        "project": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "qiskit": qiskit.__version__,
        "qiskit_ibm_runtime": qiskit_ibm_runtime.__version__,
        "qiskit_aer": qiskit_aer.__version__,
    }


def _service(instance: str | None) -> QiskitRuntimeService:
    return QiskitRuntimeService(instance=instance) if instance else QiskitRuntimeService()


class NamedSpec(Protocol):
    name: str


def _extract_counts(specs: list[NamedSpec], primitive_result) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    if len(primitive_result) != len(specs):
        raise ValueError(
            f"Expected {len(specs)} results, received {len(primitive_result)}"
        )
    for spec, pub_result in zip(specs, primitive_result, strict=True):
        if spec.name in counts:
            raise ValueError(f"Duplicate circuit name: {spec.name}")
        joint = pub_result.join_data(REGISTER_ORDER)
        raw_counts = joint.get_counts()
        if not raw_counts:
            raise ValueError(f"Primitive returned no counts for {spec.name}")
        parsed: dict[str, int] = {}
        for key, value in raw_counts.items():
            if not isinstance(key, str):
                raise ValueError(f"Non-string count key for {spec.name}: {key!r}")
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"Invalid count for {spec.name}: {key}={value!r}")
            parsed[key] = value
        counts[spec.name] = parsed
    return counts


def _save_analysis(run_dir: Path, counts: dict[str, dict[str, int]]) -> dict:
    analysis = analyze_counts(experiment_specs(), counts)
    write_json(run_dir / "counts.json", counts)
    write_json(run_dir / "analysis.json", analysis)
    write_tables(run_dir, analysis)
    plot_summary(run_dir, analysis)
    plot_axis_summary(run_dir, analysis)
    return analysis


def simulate(output_root: Path, shots: int, seed: int) -> Path:
    run_dir = output_root / f"simulator_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    specs, circuits = build_experiment()
    simulator = AerSimulator(seed_simulator=seed)
    sampler = BackendSamplerV2(backend=simulator)
    result = sampler.run(circuits, shots=shots).result()
    counts = _extract_counts(specs, result)
    analysis = _save_analysis(run_dir, counts)
    write_json(
        run_dir / "manifest.json",
        {
            "kind": "ideal_simulation",
            "shots": shots,
            "seed": seed,
            "software": software_manifest(),
            "circuits": [asdict(spec) | {"name": spec.name} for spec in specs],
            "summary": analysis["summary"],
        },
    )
    return run_dir


def _qargs_supported(target, operation: str, qargs: tuple[int, ...]) -> bool:
    try:
        properties = target[operation]
    except KeyError:
        return False
    return qargs in properties or None in properties


def _conservative_duration(circuit, target) -> tuple[float, str]:
    """Estimate one-shot duration, including a documented control-flow reserve.

    IBM's public target currently does not attach a duration to ``if_else``.
    When Qiskit's scheduler cannot estimate the complete circuit, sum all
    published instruction durations serially and reserve 500 microseconds for
    every conditional. This is intentionally far larger than gate-scale timing
    and is only a budget tripwire; IBM's server-side cap remains authoritative.
    """

    try:
        return (
            float(circuit.estimate_duration(target, unit="s")),
            "qiskit_target_critical_path",
        )
    except QiskitError:
        duration = 0.0
        for instruction in circuit.data:
            name = instruction.operation.name
            if name == "if_else":
                duration += CONTROL_FLOW_ALLOWANCE_SECONDS
                continue
            if name == "barrier":
                continue
            qargs = tuple(circuit.find_bit(qubit).index for qubit in instruction.qubits)
            operation_properties = target[name]
            properties = operation_properties.get(qargs, operation_properties.get(None))
            instruction_duration = getattr(properties, "duration", None)
            if instruction_duration is None:
                # Virtual frame changes such as RZ legitimately have zero time.
                if name == "rz":
                    continue
                raise RuntimeError(
                    f"No duration metadata for {name} on qubits {qargs}; "
                    "cannot enforce the local preflight allowance"
                )
            duration += float(instruction_duration)
        return duration, "serial_sum_plus_500us_per_if_else"


def _live_preflight(
    *,
    backend_name: str,
    instance: str | None,
    physical_qubits: list[int],
    shots: int,
    max_qpu_seconds: int,
) -> tuple[Any, list[CircuitSpec], list, dict]:
    if max_qpu_seconds > HARD_MAX_QPU_SECONDS:
        raise ValueError(
            f"Refusing cap {max_qpu_seconds}s; hard project ceiling is "
            f"{HARD_MAX_QPU_SECONDS}s"
        )
    if max_qpu_seconds < 1:
        raise ValueError("max_qpu_seconds must be at least 1")
    if len(physical_qubits) != 3 or len(set(physical_qubits)) != 3:
        raise ValueError("Exactly three distinct physical qubits are required")
    if shots <= 0 or shots > 4096:
        raise ValueError("shots must be in [1, 4096]")

    service = _service(instance)
    backend = service.backend(backend_name)
    status = backend.status()
    if not status.operational:
        raise RuntimeError(f"Backend {backend_name} is not operational")

    operations = set(backend.target.operation_names)
    required = {"if_else", "measure_2", "cz"}
    missing = sorted(required - operations)
    if missing:
        raise RuntimeError(f"Backend lacks required operations: {missing}")

    for qubit in physical_qubits[:2]:
        if not _qargs_supported(backend.target, "measure_2", (qubit,)):
            raise RuntimeError(f"measure_2 is unavailable on physical qubit {qubit}")
    for left, right in zip(physical_qubits, physical_qubits[1:]):
        if not (
            _qargs_supported(backend.target, "cz", (left, right))
            or _qargs_supported(backend.target, "cz", (right, left))
        ):
            raise RuntimeError(f"No native CZ edge between {left} and {right}")

    specs, circuits = build_experiment(optimized_mid_circuit_measurement=True)
    pass_manager = generate_preset_pass_manager(
        backend=backend,
        optimization_level=1,
        initial_layout=physical_qubits,
    )
    isa_circuits = pass_manager.run(circuits)

    circuit_metrics = []
    raw_seconds = 0.0
    for spec, circuit in zip(specs, isa_circuits, strict=True):
        duration, duration_method = _conservative_duration(circuit, backend.target)
        raw_seconds += duration * shots
        circuit_metrics.append(
            {
                "name": spec.name,
                "depth": circuit.depth(),
                "size": circuit.size(),
                "operations": dict(circuit.count_ops()),
                "estimated_duration_seconds_per_shot": duration,
                "duration_estimation_method": duration_method,
            }
        )

    # Reserve at least half the requested server-side budget for system overhead.
    raw_limit = max_qpu_seconds * 0.5
    if raw_seconds > raw_limit:
        raise RuntimeError(
            f"Estimated raw duration {raw_seconds:.6f}s exceeds conservative "
            f"preflight allowance {raw_limit:.3f}s"
        )

    calibration = getattr(backend.properties(), "last_update_date", None)
    payload = {
        "kind": "live_preflight_no_submission",
        "backend": backend_name,
        "instance": instance,
        "operational": status.operational,
        "pending_jobs": status.pending_jobs,
        "backend_version": backend.backend_version,
        "processor_type": getattr(backend, "processor_type", None),
        "calibration_last_update": calibration,
        "physical_qubits": physical_qubits,
        "shots": shots,
        "physical_circuit_count": len(isa_circuits),
        "total_shots": shots * len(isa_circuits),
        "estimated_raw_circuit_seconds": raw_seconds,
        "preflight_raw_allowance_seconds": raw_limit,
        "control_flow_allowance_seconds_per_if_else": CONTROL_FLOW_ALLOWANCE_SECONDS,
        "server_side_max_execution_seconds": max_qpu_seconds,
        "required_operations": sorted(required),
        "circuit_metrics": circuit_metrics,
        "software": software_manifest(),
        "submission_performed": False,
    }
    return backend, specs, isa_circuits, payload


def preflight(
    output_root: Path,
    *,
    backend_name: str,
    instance: str | None,
    physical_qubits: list[int],
    shots: int,
    max_qpu_seconds: int,
) -> Path:
    _, _, _, payload = _live_preflight(
        backend_name=backend_name,
        instance=instance,
        physical_qubits=physical_qubits,
        shots=shots,
        max_qpu_seconds=max_qpu_seconds,
    )
    run_dir = output_root / f"preflight_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "preflight.json", payload)
    return run_dir


def run_hardware(
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
        raise RuntimeError("Hardware submission requires the literal --submit flag")

    backend, specs, isa_circuits, preflight_payload = _live_preflight(
        backend_name=backend_name,
        instance=instance,
        physical_qubits=physical_qubits,
        shots=shots,
        max_qpu_seconds=max_qpu_seconds,
    )
    run_dir = output_root / f"hardware_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "preflight.json", preflight_payload)

    sampler = SamplerV2(mode=backend)
    sampler.options.max_execution_time = max_qpu_seconds
    sampler.options.environment.job_tags = [
        "kingston-live-teleportation",
        f"protocol-{__version__}",
    ]
    job = sampler.run(isa_circuits, shots=shots)
    submission = {
        "job_id": job.job_id(),
        "backend": backend_name,
        "instance": instance,
        "shots": shots,
        "physical_qubits": physical_qubits,
        "max_qpu_seconds": max_qpu_seconds,
        "submitted_at": utc_stamp(),
        "status_at_save": str(job.status()),
    }
    write_json(run_dir / "submission.json", submission)
    result = job.result()
    counts = _extract_counts(specs, result)
    analysis = _save_analysis(run_dir, counts)
    metrics = job.metrics()
    write_json(run_dir / "job_metrics.json", metrics)
    write_json(
        run_dir / "result_manifest.json",
        {
            **submission,
            "completed_at": utc_stamp(),
            "final_status": str(job.status()),
            "ibm_quantum_seconds": metrics.get("usage", {}).get("quantum_seconds"),
            "summary": analysis["summary"],
            "contrasts": analysis["contrasts"],
        },
    )
    return run_dir, job.job_id()


def resume_hardware(run_dir: Path) -> tuple[Path, str]:
    submission = read_json(run_dir / "submission.json")
    service = _service(submission.get("instance"))
    job = service.job(submission["job_id"])
    result = job.result()
    counts = _extract_counts(experiment_specs(), result)
    analysis = _save_analysis(run_dir, counts)
    metrics = job.metrics()
    write_json(run_dir / "job_metrics.json", metrics)
    write_json(
        run_dir / "result_manifest.json",
        {
            **submission,
            "completed_at": utc_stamp(),
            "final_status": str(job.status()),
            "ibm_quantum_seconds": metrics.get("usage", {}).get("quantum_seconds"),
            "summary": analysis["summary"],
            "contrasts": analysis["contrasts"],
        },
    )
    return run_dir, submission["job_id"]


def run_diagnostic(
    output_root: Path,
    *,
    backend_name: str,
    instance: str | None,
    physical_qubits: list[int],
    shots: int,
    max_qpu_seconds: int,
    submit: bool,
) -> tuple[Path, str]:
    """Run a small exploratory test of each register/correction pairing."""

    if not submit:
        raise RuntimeError("Diagnostic submission requires the literal --submit flag")
    if max_qpu_seconds > 8:
        raise ValueError("The exploratory diagnostic has a hard 8-QPU-second ceiling")
    if len(physical_qubits) != 3 or len(set(physical_qubits)) != 3:
        raise ValueError("Exactly three distinct physical qubits are required")
    if shots <= 0 or shots > 1024:
        raise ValueError("diagnostic shots must be in [1, 1024]")

    service = _service(instance)
    backend = service.backend(backend_name)
    if not backend.status().operational:
        raise RuntimeError(f"Backend {backend_name} is not operational")
    specs, circuits = build_diagnostics()
    pass_manager = generate_preset_pass_manager(
        backend=backend,
        optimization_level=1,
        initial_layout=physical_qubits,
    )
    isa_circuits = pass_manager.run(circuits)
    estimated_seconds = sum(
        _conservative_duration(circuit, backend.target)[0] * shots
        for circuit in isa_circuits
    )
    if estimated_seconds > max_qpu_seconds * 0.5:
        raise RuntimeError("Diagnostic exceeds its conservative preflight allowance")

    run_dir = output_root / f"diagnostic_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    sampler = SamplerV2(mode=backend)
    sampler.options.max_execution_time = max_qpu_seconds
    sampler.options.environment.job_tags = [
        "kingston-live-teleportation",
        "exploratory-feedforward-diagnostic",
    ]
    job = sampler.run(isa_circuits, shots=shots)
    submission = {
        "kind": "exploratory_register_correction_diagnostic",
        "job_id": job.job_id(),
        "backend": backend_name,
        "instance": instance,
        "physical_qubits": physical_qubits,
        "shots": shots,
        "max_qpu_seconds": max_qpu_seconds,
        "estimated_seconds_with_control_flow_allowance": estimated_seconds,
        "submitted_at": utc_stamp(),
    }
    write_json(run_dir / "submission.json", submission)
    result = job.result()
    counts = _extract_counts(specs, result)

    rows = []
    for spec in specs:
        successes = 0
        total = 0
        for key, count in counts[spec.name].items():
            bits = key.replace(" ", "").zfill(3)
            m0, m1, outcome = int(bits[2]), int(bits[1]), int(bits[0])
            expected = m0 if spec.source == "m0" else m1
            successes += count if outcome == expected else 0
            total += count
        rows.append(
            {
                "name": spec.name,
                "source": spec.source,
                "correction": spec.correction,
                "successes": successes,
                "shots": total,
                "agreement": successes / total,
            }
        )
    metrics = job.metrics()
    write_json(run_dir / "counts.json", counts)
    write_json(run_dir / "diagnostic_results.json", rows)
    write_json(run_dir / "job_metrics.json", metrics)
    write_json(
        run_dir / "result_manifest.json",
        {
            **submission,
            "final_status": str(job.status()),
            "ibm_quantum_seconds": metrics.get("usage", {}).get("quantum_seconds"),
            "results": rows,
        },
    )
    return run_dir, job.job_id()


def run_sequence_diagnostic(
    output_root: Path,
    *,
    backend_name: str,
    instance: str | None,
    physical_qubits: list[int],
    shots: int,
    max_qpu_seconds: int,
    submit: bool,
) -> tuple[Path, str]:
    """Test X→Z versus Z→X back-to-back conditional behavior."""

    if not submit:
        raise RuntimeError("Sequence diagnostic requires the literal --submit flag")
    if max_qpu_seconds > 5:
        raise ValueError("The sequence diagnostic has a hard 5-QPU-second ceiling")
    if len(physical_qubits) != 3 or len(set(physical_qubits)) != 3:
        raise ValueError("Exactly three distinct physical qubits are required")
    if shots <= 0 or shots > 1024:
        raise ValueError("diagnostic shots must be in [1, 1024]")

    service = _service(instance)
    backend = service.backend(backend_name)
    if not backend.status().operational:
        raise RuntimeError(f"Backend {backend_name} is not operational")
    specs, circuits = build_sequence_diagnostics()
    pass_manager = generate_preset_pass_manager(
        backend=backend,
        optimization_level=1,
        initial_layout=physical_qubits,
    )
    isa_circuits = pass_manager.run(circuits)
    estimated_seconds = sum(
        _conservative_duration(circuit, backend.target)[0] * shots
        for circuit in isa_circuits
    )
    if estimated_seconds > max_qpu_seconds * 0.5:
        raise RuntimeError("Sequence diagnostic exceeds its preflight allowance")

    run_dir = output_root / f"sequence_diagnostic_{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    sampler = SamplerV2(mode=backend)
    sampler.options.max_execution_time = max_qpu_seconds
    sampler.options.environment.job_tags = [
        "kingston-live-teleportation",
        "exploratory-sequence-diagnostic",
    ]
    job = sampler.run(isa_circuits, shots=shots)
    submission = {
        "kind": "exploratory_conditional_sequence_diagnostic",
        "job_id": job.job_id(),
        "backend": backend_name,
        "instance": instance,
        "physical_qubits": physical_qubits,
        "shots": shots,
        "max_qpu_seconds": max_qpu_seconds,
        "estimated_seconds_with_control_flow_allowance": estimated_seconds,
        "submitted_at": utc_stamp(),
    }
    write_json(run_dir / "submission.json", submission)
    result = job.result()
    counts = _extract_counts(specs, result)

    rows = []
    for spec in specs:
        successes = 0
        total = 0
        for key, count in counts[spec.name].items():
            bits = key.replace(" ", "").zfill(3)
            m0, m1, outcome = int(bits[2]), int(bits[1]), int(bits[0])
            expected = m0 if spec.basis == "X" else m1
            successes += count if outcome == expected else 0
            total += count
        rows.append(
            {
                "name": spec.name,
                "order": spec.order,
                "measurement_basis": spec.basis,
                "expected_from": "m0" if spec.basis == "X" else "m1",
                "successes": successes,
                "shots": total,
                "agreement": successes / total,
            }
        )
    metrics = job.metrics()
    write_json(run_dir / "counts.json", counts)
    write_json(run_dir / "diagnostic_results.json", rows)
    write_json(run_dir / "job_metrics.json", metrics)
    write_json(
        run_dir / "result_manifest.json",
        {
            **submission,
            "final_status": str(job.status()),
            "ibm_quantum_seconds": metrics.get("usage", {}).get("quantum_seconds"),
            "results": rows,
        },
    )
    return run_dir, job.job_id()
