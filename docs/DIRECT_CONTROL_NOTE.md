# Interpretation of the direct estimator

KLT-001's six direct circuits were intended as a preparation-and-readout
baseline on the output qubit. The transpiled ISA makes the narrower operational
meaning important.

For the positive Pauli eigenstates, the preparation rotation and inverse
measurement-basis rotation cancel under optimization, leaving only terminal
measurement. For the negative eigenstates, the ISA reduces to X followed by
measurement. Thus the reported direct mean, 0.993652, is best described as an
**output-qubit computational-basis SPAM/readout baseline after transpiler
optimization**, not an independent six-state state-preparation benchmark.

This clarification changes no circuit, count, estimate, interval, or KLT-001
conclusion. It prevents the high direct value from being over-interpreted.
