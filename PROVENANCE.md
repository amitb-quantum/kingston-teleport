# Provenance and protocol identity

This repository separates the completed study from prospective work. The
identifiers below are immutable scientific labels, not marketing release names.

## KLT-001 v1.0.0 — completed Kingston experiment

- Frozen preregistration: `PREREGISTRATION.md`
- Curated bundle: `results/kingston-2026-09-15/`
- First repository commit containing the code and complete bundle: `8954cf4`
- Documentation commit interpreting the frozen bundle: `b79b8b5`
- Hardware job IDs: `daktp8c62pvc739q1t6g`, `daktrsgnf91c73crdckg`,
  `daktsvgnf91c73crddrg`
- IBM-reported use: 7, 3, and 3 QPU seconds (13 total)

The bundle checksum manifest covers every file in the bundle other than the
manifest itself. `kingston-teleport verify-results` checks that coverage,
validates all SHA-256 hashes, regenerates `analysis.json` exactly from
`counts.json`, and checks the three job records without contacting IBM.

## KLT-002 v1.0.0 — preregistered follow-up, not executed

- Frozen design: `preregistrations/KLT-002_DELAY_CORRECTION_PREREGISTRATION.md`
- Timing audit: `docs/TIMING_AUDIT.md`
- Code entry points: `followup.py` and `followup_workflow.py`
- Status at publication: local simulation and read-only IBM preflight only;
  no KLT-002 QPU submission

Each future artifact manifest records the package, Python, Qiskit,
qiskit-ibm-runtime, Qiskit Aer, operating-system, backend-version, calibration,
layout, and budget metadata available at that stage. A hardware result is not
complete unless it also records the job ID, server-reported QPU seconds, final
status, raw counts, result metadata, and the exact transpiled QPY circuits.

## Environment

`environment.yml` and `pyproject.toml` constrain the software family. Exact
versions used for an artifact are written into that artifact's manifest. IBM
credentials and account configuration are deliberately excluded.
