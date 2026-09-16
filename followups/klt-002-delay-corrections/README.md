# KLT-002 preparation artifacts

Status: **completed on hardware and verified offline**.

The primary result report is [`RESULTS.md`](RESULTS.md). IBM job
`dal13ss62pvc739q7gig` completed successfully using 10 QPU seconds, below the
preregistered 11.6768-second estimate and 20-second cap.

The `simulation_20260916T034028Z` directory contains a 4,096-shot-per-circuit
ideal Aer validation. All nine modes have mean fidelity 1.0, confirming circuit
and offline-correction semantics in the ideal model.

The `preflight_20260916T034034Z` directory contains a read-only live audit of
`ibm_kingston` and the exact 54 transpiled ISA circuits. At that snapshot:

- backend operational, zero pending jobs;
- selected layout preserved as `[147, 148, 149]` at input and output for every
  circuit;
- all 2/4/8 μs delays survived on physical output q149;
- 512 shots × 54 circuits = 27,648 executions;
- IBM quick usage estimate 11.6768 QPU seconds, below the frozen 20-second cap;
- submission flag recorded as false.

Each directory's `SHA256SUMS` covers every other file in that directory. The
preflight QPY is the machine-readable ISA evidence. These artifacts are design
validation and do not contain hardware outcome data.

The `hardware_20260916T035011Z` directory contains the raw hardware counts,
exact analysis, IBM job metrics, exact ISA QPY, all 54 scheduler-timing records,
and a complete checksum manifest. The `derived` directory contains a
reproducible result figure, timing summary, preregistered decision record, and
its own checksums.
