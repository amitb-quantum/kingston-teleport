"""Command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

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


if __name__ == "__main__":
    main()
