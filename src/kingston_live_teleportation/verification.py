"""Offline integrity and reproducibility checks for committed result bundles."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .analysis import analyze_counts
from .circuits import experiment_specs
from .io import read_json


def _sha256_bytes(payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def verify_results(repo_root: Path) -> dict:
    """Verify hashes, coverage, published analysis, and recorded QPU usage."""

    repo_root = repo_root.resolve()
    bundle = repo_root / "results" / "kingston-2026-09-15"
    manifest_path = bundle / "SHA256SUMS"
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
        path.relative_to(repo_root).as_posix()
        for path in bundle.rglob("*")
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
        path = repo_root / relative
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
        "files_verified": len(declared),
        "crlf_materializations_verified_as_canonical_lf": normalized_text_files,
        "analysis_exactly_regenerated": True,
        "job_ids": [job["job_id"] for job in jobs],
        "qpu_seconds_by_job": qpu_seconds,
        "total_qpu_seconds": sum(qpu_seconds),
    }
