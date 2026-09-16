"""Offline integrity and reproducibility checks for committed result bundles."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .analysis import analyze_counts
from .circuits import experiment_specs
from .followup import analyze_followup_counts, followup_specs
from .io import read_json
from .protocols import FOLLOWUP_PROTOCOL_ID, FOLLOWUP_PROTOCOL_VERSION


def _sha256_bytes(payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def _verify_hash_manifest(
    manifest_path: Path,
    *,
    resolution_root: Path,
    coverage_root: Path,
) -> tuple[int, list[str]]:
    if not manifest_path.is_file():
        raise ValueError(f"Missing checksum manifest: {manifest_path}")

    declared: dict[str, str] = {}
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            expected_hash, relative = line.split(maxsplit=1)
        except ValueError as exc:
            raise ValueError(f"Malformed SHA256SUMS line {line_number}") from exc
        relative = relative.strip().replace("\\", "/")
        if relative in declared:
            raise ValueError(f"Duplicate checksum entry: {relative}")
        declared[relative] = expected_hash.lower()

    actual_files = {
        path.relative_to(resolution_root).as_posix()
        for path in coverage_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if set(declared) != actual_files:
        raise ValueError(
            "Checksum coverage mismatch; "
            f"missing={sorted(actual_files - set(declared))}, "
            f"extra={sorted(set(declared) - actual_files)}"
        )

    normalized_text_files: list[str] = []
    for relative, expected_hash in declared.items():
        path = resolution_root / relative
        payload = path.read_bytes()
        observed_hash = _sha256_bytes(payload)
        # Git for Windows may materialize tracked LF text as CRLF even though
        # SHA256SUMS correctly records the archived repository bytes.
        if observed_hash != expected_hash and b"\r\n" in payload:
            canonical_hash = _sha256_bytes(payload.replace(b"\r\n", b"\n"))
            if canonical_hash == expected_hash:
                observed_hash = canonical_hash
                normalized_text_files.append(relative)
        if observed_hash != expected_hash:
            raise ValueError(
                f"Checksum mismatch for {relative}: "
                f"expected {expected_hash}, observed {observed_hash}"
            )
    return len(declared), normalized_text_files


def verify_results(repo_root: Path) -> dict:
    """Verify hashes, coverage, published analysis, and recorded QPU usage."""

    repo_root = repo_root.resolve()
    bundle = repo_root / "results" / "kingston-2026-09-15"
    files_verified, normalized_text_files = _verify_hash_manifest(
        bundle / "SHA256SUMS",
        resolution_root=repo_root,
        coverage_root=bundle,
    )

    counts = read_json(bundle / "main" / "counts.json")
    regenerated = analyze_counts(experiment_specs(), counts)
    published = read_json(bundle / "main" / "analysis.json")
    if regenerated != published:
        raise ValueError("Published analysis is not exactly reproducible from counts.json")

    result_manifests = [
        bundle / "main" / "result_manifest.json",
        bundle / "register-diagnostic" / "result_manifest.json",
        bundle / "sequence-diagnostic" / "result_manifest.json",
    ]
    jobs = [read_json(path) for path in result_manifests]
    if any(job.get("final_status") != "DONE" for job in jobs):
        raise ValueError("At least one published hardware job is not recorded as DONE")
    qpu_seconds = [job.get("ibm_quantum_seconds") for job in jobs]
    if qpu_seconds != [7, 3, 3]:
        raise ValueError(f"Unexpected published QPU usage: {qpu_seconds}")

    return {
        "status": "verified",
        "bundle": str(bundle.relative_to(repo_root)),
        "files_verified": files_verified,
        "crlf_materializations_verified_as_canonical_lf": normalized_text_files,
        "analysis_exactly_regenerated": True,
        "job_ids": [job["job_id"] for job in jobs],
        "qpu_seconds_by_job": qpu_seconds,
        "total_qpu_seconds": sum(qpu_seconds),
    }


def verify_followup_results(repo_root: Path, run_dir: Path | None = None) -> dict:
    """Verify the KLT-002 hardware bundle without IBM credentials or network."""

    repo_root = repo_root.resolve()
    if run_dir is None:
        run_dir = (
            repo_root
            / "followups"
            / "klt-002-delay-corrections"
            / "artifacts"
            / "hardware_20260916T035011Z"
        )
    elif not run_dir.is_absolute():
        run_dir = repo_root / run_dir
    run_dir = run_dir.resolve()
    files_verified, normalized_text_files = _verify_hash_manifest(
        run_dir / "SHA256SUMS",
        resolution_root=run_dir,
        coverage_root=run_dir,
    )

    counts = read_json(run_dir / "counts.json")
    regenerated = analyze_followup_counts(counts)
    published = read_json(run_dir / "analysis.json")
    if regenerated != published:
        raise ValueError("KLT-002 analysis is not exactly reproducible from counts.json")
    if any(sum(circuit_counts.values()) != 512 for circuit_counts in counts.values()):
        raise ValueError("KLT-002 does not contain exactly 512 shots per circuit")

    manifest = read_json(run_dir / "result_manifest.json")
    metrics = read_json(run_dir / "job_metrics.json")
    if manifest.get("final_status") != "DONE":
        raise ValueError("KLT-002 job is not recorded as DONE")
    if manifest.get("job_id") != "dal13ss62pvc739q7gig":
        raise ValueError(f"Unexpected KLT-002 job ID: {manifest.get('job_id')}")
    if manifest.get("ibm_quantum_seconds") != 10:
        raise ValueError("Unexpected KLT-002 QPU usage in result manifest")
    if metrics.get("usage", {}).get("quantum_seconds") != 10:
        raise ValueError("Unexpected KLT-002 QPU usage in job metrics")
    if manifest.get("scheduler_timing_requested") is not True:
        raise ValueError("KLT-002 did not record scheduler-timing as requested")

    timing = read_json(run_dir / "scheduler_timing_metadata.json")
    specs = followup_specs()
    if len(timing) != len(specs):
        raise ValueError(f"Expected {len(specs)} timing records, got {len(timing)}")
    for spec, record in zip(specs, timing, strict=True):
        metadata = record.get("circuit_metadata", {})
        expected = {
            "protocol_id": FOLLOWUP_PROTOCOL_ID,
            "protocol_version": FOLLOWUP_PROTOCOL_VERSION,
            "mode": spec.mode,
            "state": spec.state,
            "axis": spec.axis,
            "eigenvalue": spec.eigenvalue,
            "delay_us": spec.delay_us,
        }
        if metadata != expected:
            raise ValueError(
                f"Timing metadata mismatch for {spec.name}: {metadata!r}"
            )
        scheduler = record.get("compilation", {}).get("scheduler_timing", {})
        if not isinstance(scheduler.get("circuit_duration"), int) or scheduler[
            "circuit_duration"
        ] <= 0:
            raise ValueError(f"Invalid scheduler duration for {spec.name}")
        if not scheduler.get("timing"):
            raise ValueError(f"Missing scheduler event text for {spec.name}")

    return {
        "status": "verified",
        "bundle": str(run_dir.relative_to(repo_root)),
        "files_verified": files_verified,
        "crlf_materializations_verified_as_canonical_lf": normalized_text_files,
        "analysis_exactly_regenerated": True,
        "circuits": len(specs),
        "shots_per_circuit": 512,
        "scheduler_timing_records": len(timing),
        "job_id": manifest["job_id"],
        "qpu_seconds": manifest["ibm_quantum_seconds"],
    }
