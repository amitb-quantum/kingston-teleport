"""Command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

from .followup_workflow import (
    FOLLOWUP_HARD_MAX_QPU_SECONDS,
    FOLLOWUP_SHOTS,
    preflight_followup,
    run_followup_hardware,
    simulate_followup,
)
from .followup_reporting import build_followup_report_artifacts
from .verification import verify_followup_results, verify_results
from .workflow import (
    HARD_MAX_QPU_SECONDS,
    preflight,
    resume_hardware,
    run_diagnostic,
    run_hardware,
    run_sequence_diagnostic,
    simulate,
)


def _common_live_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", default="ibm_kingston")
    parser.add_argument("--instance", default="QEC")
    parser.add_argument(
        "--physical-qubits",
        type=int,
        nargs=3,
        default=[147, 148, 149],
        metavar=("INPUT", "ANCILLA", "OUTPUT"),
    )
    parser.add_argument("--shots", type=int, default=1024)
    parser.add_argument(
        "--max-qpu-seconds",
        type=int,
        default=HARD_MAX_QPU_SECONDS,
        help=f"server-side QPU usage cap; hard ceiling {HARD_MAX_QPU_SECONDS}",
    )
    parser.add_argument("--output-root", type=Path, default=Path("runs"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kingston-teleport",
        description="Budget-capped IBM Kingston dynamic teleportation benchmark",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulation = subparsers.add_parser("simulate", help="run ideal Aer validation")
    simulation.add_argument("--shots", type=int, default=2048)
    simulation.add_argument("--seed", type=int, default=240915)
    simulation.add_argument("--output-root", type=Path, default=Path("runs"))

    live_preflight = subparsers.add_parser(
        "preflight", help="read-only backend audit and transpilation"
    )
    _common_live_arguments(live_preflight)

    hardware = subparsers.add_parser("run", help="submit one capped hardware job")
    _common_live_arguments(hardware)
    hardware.add_argument(
        "--submit",
        action="store_true",
        help="required literal authorization gate for QPU submission",
    )

    resume = subparsers.add_parser("resume", help="retrieve an already-submitted job")
    resume.add_argument("--run-dir", required=True, type=Path)

    diagnostic = subparsers.add_parser(
        "diagnose", help="run the capped exploratory register/correction control"
    )
    diagnostic.add_argument("--backend", default="ibm_kingston")
    diagnostic.add_argument("--instance", default="QEC")
    diagnostic.add_argument(
        "--physical-qubits", type=int, nargs=3, default=[147, 148, 149]
    )
    diagnostic.add_argument("--shots", type=int, default=512)
    diagnostic.add_argument("--max-qpu-seconds", type=int, default=8)
    diagnostic.add_argument("--output-root", type=Path, default=Path("runs"))
    diagnostic.add_argument("--submit", action="store_true")

    sequence = subparsers.add_parser(
        "diagnose-sequence", help="compare two back-to-back conditional orders"
    )
    sequence.add_argument("--backend", default="ibm_kingston")
    sequence.add_argument("--instance", default="QEC")
    sequence.add_argument(
        "--physical-qubits", type=int, nargs=3, default=[147, 148, 149]
    )
    sequence.add_argument("--shots", type=int, default=512)
    sequence.add_argument("--max-qpu-seconds", type=int, default=5)
    sequence.add_argument("--output-root", type=Path, default=Path("runs"))
    sequence.add_argument("--submit", action="store_true")

    followup_sim = subparsers.add_parser(
        "simulate-followup", help="run the frozen KLT-002 design on Aer"
    )
    followup_sim.add_argument("--shots", type=int, default=4096)
    followup_sim.add_argument("--seed", type=int, default=260915)
    followup_sim.add_argument(
        "--output-root",
        type=Path,
        default=Path("followups/klt-002-delay-corrections/artifacts"),
    )

    for name, help_text in (
        ("preflight-followup", "read-only KLT-002 backend and ISA audit"),
        ("run-followup", "submit the frozen, capped KLT-002 hardware job"),
    ):
        followup = subparsers.add_parser(name, help=help_text)
        followup.add_argument("--backend", default="ibm_kingston")
        followup.add_argument("--instance", default="QEC")
        followup.add_argument(
            "--physical-qubits", type=int, nargs=3, default=[147, 148, 149]
        )
        followup.add_argument("--shots", type=int, default=FOLLOWUP_SHOTS)
        followup.add_argument(
            "--max-qpu-seconds", type=int, default=FOLLOWUP_HARD_MAX_QPU_SECONDS
        )
        followup.add_argument(
            "--output-root",
            type=Path,
            default=Path("followups/klt-002-delay-corrections/artifacts"),
        )
        if name == "run-followup":
            followup.add_argument("--submit", action="store_true")

    verify = subparsers.add_parser(
        "verify-results", help="offline integrity and result regeneration audit"
    )
    verify.add_argument("--repo-root", type=Path, default=Path("."))
    verify_followup = subparsers.add_parser(
        "verify-followup", help="offline KLT-002 integrity and regeneration audit"
    )
    verify_followup.add_argument("--repo-root", type=Path, default=Path("."))
    verify_followup.add_argument("--run-dir", type=Path)
    report_followup = subparsers.add_parser(
        "report-followup", help="build KLT-002 derived timing table and figure"
    )
    report_followup.add_argument(
        "--run-dir",
        type=Path,
        default=Path(
            "followups/klt-002-delay-corrections/artifacts/"
            "hardware_20260916T035011Z"
        ),
    )
    report_followup.add_argument(
        "--output-dir",
        type=Path,
        default=Path("followups/klt-002-delay-corrections/derived"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "simulate":
        run_dir = simulate(args.output_root, args.shots, args.seed)
        print(f"Simulation complete: {run_dir}")
    elif args.command == "preflight":
        run_dir = preflight(
            args.output_root,
            backend_name=args.backend,
            instance=args.instance,
            physical_qubits=args.physical_qubits,
            shots=args.shots,
            max_qpu_seconds=args.max_qpu_seconds,
        )
        print(f"Preflight complete; no QPU submission: {run_dir}")
    elif args.command == "run":
        run_dir, job_id = run_hardware(
            args.output_root,
            backend_name=args.backend,
            instance=args.instance,
            physical_qubits=args.physical_qubits,
            shots=args.shots,
            max_qpu_seconds=args.max_qpu_seconds,
            submit=args.submit,
        )
        print(f"Hardware run complete: {run_dir}")
        print(f"IBM job ID: {job_id}")
    elif args.command == "resume":
        run_dir, job_id = resume_hardware(args.run_dir)
        print(f"Hardware result recovered: {run_dir}")
        print(f"IBM job ID: {job_id}")
    elif args.command == "diagnose":
        run_dir, job_id = run_diagnostic(
            args.output_root,
            backend_name=args.backend,
            instance=args.instance,
            physical_qubits=args.physical_qubits,
            shots=args.shots,
            max_qpu_seconds=args.max_qpu_seconds,
            submit=args.submit,
        )
        print(f"Exploratory diagnostic complete: {run_dir}")
        print(f"IBM job ID: {job_id}")
    elif args.command == "diagnose-sequence":
        run_dir, job_id = run_sequence_diagnostic(
            args.output_root,
            backend_name=args.backend,
            instance=args.instance,
            physical_qubits=args.physical_qubits,
            shots=args.shots,
            max_qpu_seconds=args.max_qpu_seconds,
            submit=args.submit,
        )
        print(f"Sequence diagnostic complete: {run_dir}")
        print(f"IBM job ID: {job_id}")
    elif args.command == "simulate-followup":
        run_dir = simulate_followup(args.output_root, args.shots, args.seed)
        print(f"KLT-002 simulation complete; no QPU submission: {run_dir}")
    elif args.command == "preflight-followup":
        run_dir = preflight_followup(
            args.output_root,
            backend_name=args.backend,
            instance=args.instance,
            physical_qubits=args.physical_qubits,
            shots=args.shots,
            max_qpu_seconds=args.max_qpu_seconds,
        )
        print(f"KLT-002 preflight complete; no QPU submission: {run_dir}")
    elif args.command == "run-followup":
        run_dir, job_id = run_followup_hardware(
            args.output_root,
            backend_name=args.backend,
            instance=args.instance,
            physical_qubits=args.physical_qubits,
            shots=args.shots,
            max_qpu_seconds=args.max_qpu_seconds,
            submit=args.submit,
        )
        print(f"KLT-002 hardware run complete: {run_dir}")
        print(f"IBM job ID: {job_id}")
    elif args.command == "verify-results":
        result = verify_results(args.repo_root)
        print(
            f"Verified {result['files_verified']} files, exact analysis "
            f"regeneration, and {result['total_qpu_seconds']} total QPU seconds."
        )
    elif args.command == "verify-followup":
        result = verify_followup_results(args.repo_root, args.run_dir)
        print(
            f"Verified KLT-002 job {result['job_id']}: "
            f"{result['files_verified']} files, {result['circuits']} circuits, "
            f"{result['scheduler_timing_records']} timing records, and "
            f"{result['qpu_seconds']} QPU seconds."
        )
    elif args.command == "report-followup":
        output_dir = build_followup_report_artifacts(args.run_dir, args.output_dir)
        print(f"KLT-002 derived report artifacts written to: {output_dir}")


if __name__ == "__main__":
    main()
