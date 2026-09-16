# KLT-002 preparation artifacts

Status: **hardware-ready; no QPU submission performed**.

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
