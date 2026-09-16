# Results: IBM Kingston live-feed-forward teleportation

Run date: 2026-09-15/16 UTC
Backend: `ibm_kingston` (Heron R2)
Physical chain: `[147, 148, 149]`
Protocol: `0.1.0`

## Headline result

The preregistered dynamic-teleportation experiment did **not** establish
above-classical average teleportation fidelity. Live feed-forward produced a
six-state mean fidelity of `0.6574` with a stratified-bootstrap 95% interval of
`[0.6465, 0.6683]`. The interval includes the classical `2/3` threshold and the
point estimate is below it.

This is not a failure of the underlying teleportation correlations. Applying
the Pauli-frame corrections offline to the matched uncorrected shots produced
mean fidelity `0.9512 [0.9456, 0.9564]`; the direct preparation/readout control
was `0.9937 [0.9915, 0.9956]`. The uncorrected negative control was
`0.5018 [0.4894, 0.5143]` as expected.

| Estimator | Mean fidelity | 95% interval |
|---|---:|---:|
| Direct control | 0.9937 | [0.9915, 0.9956] |
| Dynamic feed-forward | 0.6574 | [0.6465, 0.6683] |
| Offline correction | 0.9512 | [0.9456, 0.9564] |
| Uncorrected control | 0.5018 | [0.4894, 0.5143] |

## Axis-resolved structure

The dynamic result was strongly anisotropic:

| Axis | Dynamic fidelity | Offline fidelity | Dynamic PTM diagonal |
|---|---:|---:|---:|
| X | 0.9331 | 0.9453 | 0.8662 |
| Y | 0.5249 | 0.9375 | 0.0498 |
| Z | 0.5142 | 0.9707 | 0.0283 |

The near-preservation of X eigenstates alongside almost complete loss of Y and
Z information resembles an effective random-X channel. That observation is a
phenomenological description, not a demonstrated hardware mechanism.

## Exploratory controls

The main result initially suggested that one Pauli correction might not be
executing. Two small, explicitly post-hoc diagnostics did not support the
simple versions of that explanation.

The first independently tested each measurement register against each
conditional correction:

| Control | Agreement |
|---|---:|
| `m0 → X` | 1.0000 |
| `m0 → Z` | 0.9824 |
| `m1 → X` | 0.9961 |
| `m1 → Z` | 0.9902 |

The second applied both corrections and compared their order:

| Order | Readout basis | Agreement |
|---|---|---:|
| X then Z | X | 0.9824 |
| X then Z | Z | 0.9941 |
| Z then X | X | 0.9688 |
| Z then X | Z | 0.9941 |

Thus, isolated register addressing, isolated conditional gates, and simple
back-to-back ordering all worked at high fidelity. The observed anisotropy
appears specific to the complete teleportation/control interaction on this
chain and calibration snapshot. Plausible contributors include latency while
the output holds a measurement-conditioned state, measurement backaction or
crosstalk, a scheduling interaction, or a calibration-specific effect. The
present data do not identify a unique cause.

## Resource use

IBM-reported QPU usage was:

| Job | IBM job ID | QPU seconds |
|---|---|---:|
| Preregistered experiment | `daktp8c62pvc739q1t6g` | 7 |
| Register/correction diagnostic | `daktrsgnf91c73crdckg` | 3 |
| Conditional-order diagnostic | `daktsvgnf91c73crddrg` | 3 |
| **Total** | | **13** |

Each job also had an IBM server-side execution cap (25, 8, and 5 seconds,
respectively). No Runtime session or error-mitigation expansion was used.

## Interpretation

The primary preregistered hypothesis was not supported. The result nevertheless
has value: high offline fidelity shows that the Bell-measurement correlations
were present, while the dynamic path introduced a large, axis-selective loss
that simple feed-forward controls did not reproduce.

This single-chain observation should not be generalized to IBM Kingston as a
whole. A follow-up study should preregister delay-matched controls, single-
correction teleportation variants, alternate chains, and a second calibration
window. No such follow-up hardware runs were performed here.

Curated machine-readable artifacts and figures are in
`results/kingston-2026-09-15/`. The original design and decision rules remain
unchanged in `PREREGISTRATION.md`.
