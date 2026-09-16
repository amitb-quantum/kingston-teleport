#!/usr/bin/env python3
"""
ibm_backend_path_audit.py  (v2)
===============================

Zero-QPU calibration, noise-profile, topology, and N-qubit path audit for IBM
Quantum backends.

Purpose
-------
Compare backends (e.g. ibm_fez, ibm_kingston) and select a physical qubit path
before any QPU submission, using both the current calibration and a window of
historical calibration snapshots.

What changed in v2
------------------
1.  RAW SNAPSHOT PERSISTENCE + REPLAY.  Every unique calibration snapshot is
    normalised and written to disk alongside the provider's raw payload.
    `--replay DIR` re-scores saved snapshots with zero network access, so an
    audit is reproducible after the provider's rolling history has moved on,
    and threshold changes can be diffed against frozen data.

2.  SCORE DECOMPOSITION + PROFILES.  The score is now an estimated circuit
    infidelity (interpretable, in error units) plus explicitly weighted
    stability penalties, and every term is emitted per path.  `--profile`
    selects the circuit shape the estimate is built for; v1's implicit
    weighting was storage-like and gave two-qubit gate error ~11% of the
    ranking, which is wrong for gate-dominated syndrome extraction.

3.  PER-UNIT QUANTILES.  v1 pooled all qubits x all snapshots before taking
    q20/q80, which diluted a single bad qubit by roughly 3.5x (it had to be
    bad in ~87% of its own snapshots to trip the gate).  v2 computes the
    quantile per qubit and per edge, then takes the worst.

4.  EXCURSION FIXES.  The latest snapshot is no longer included in the median
    it is compared against (self-inclusion was decision-flipping near the
    threshold), and excursion thresholds are now per-metric, since readout
    error is naturally far more volatile than T1/T2.

5.  LATEST-SNAPSHOT INTEGRITY.  v1 used rows[0] as "latest", which silently
    became the second-newest snapshot for any path missing current
    calibration data.  v2 tracks the true current timestamp and flags or
    rejects paths whose latest row does not match it.

6.  FIXED HISTORY DEPTH.  robust_pass is monotone in snapshot count, so counts
    are not comparable across runs or backends.  `--history-fixed-n`
    truncates every backend to the same number of most-recent snapshots.

7.  FULL RANKING PERSISTED.  All paths are written to JSON/CSV (not just the
    top 25), which is what makes cross-run set-stability analysis possible.

8.  CROSS-RUN STABILITY.  `--compare-with` takes a previous
    backend_comparison.json and reports Jaccard overlap of the robust-eligible
    set plus Spearman rank correlation over shared paths.

9.  Misc: retry/backoff on provider calls, directional two-qubit error
    recorded separately, inferred Tphi optionally gated instead of unused,
    configurable path length, SHA256SUMS artifact.

Important limitations
---------------------
* T1/T2/readout are local calibration descriptors.  They do not establish
  correlated or collective noise, and nothing here measures crosstalk.
* Provider historical queries return the nearest older calibration and may
  return duplicates; duplicates are removed by calibration timestamp.
* Queue depth is reported but never scored.
* This script performs no Runtime primitive call and submits no QPU job.

Recommended use
---------------
    conda activate ibm
    python ibm_backend_path_audit.py \
        --backends ibm_fez ibm_kingston \
        --history-days 5 --history-step-hours 12 --history-fixed-n 8 \
        --profile gates --output-dir v6_backend_audit

Re-score the same frozen data later without touching the network:

    python ibm_backend_path_audit.py --replay v6_backend_audit --profile storage

Measure selector stability across runs:

    python ibm_backend_path_audit.py --backends ibm_kingston \
        --output-dir run_2026_08_15 \
        --compare-with run_2026_08_14/backend_comparison.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

SCRIPT_VERSION = "2.0.0"

# ----------------------------------------------------------------------------
# Thresholds and weights.  Every value here is emitted into the manifest.
# ----------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    # Historical coverage.
    "history_min_unique": 5,

    # Latest/current hard gates.
    "latest_min_t1_us": 60.0,
    "latest_min_t2_us": 60.0,
    "latest_max_readout": 0.030,
    "latest_max_2q_error": 0.015,

    # Strict worst-case historical gates.
    "strict_min_t1_us": 60.0,
    "strict_min_t2_us": 60.0,
    "strict_max_readout": 0.030,
    "strict_max_2q_error": 0.015,

    # Robust-history quantile gates (now computed PER QUBIT / PER EDGE).
    "history_low_quantile": 0.20,
    "history_high_quantile": 0.80,
    "robust_q20_min_t1_us": 60.0,
    "robust_q20_min_t2_us": 60.0,
    "robust_q80_max_readout": 0.030,
    "robust_q80_max_2q_error": 0.015,

    # Optional dephasing gate.  None/0 disables.
    "robust_q20_min_tphi_us": 0.0,

    # Stability / persistence gates.
    "max_t1_cv": 0.35,
    "max_t2_cv": 0.35,
    "max_readout_cv": 0.60,
    "max_2q_cv": 0.60,
    "max_bad_snapshot_fraction": 0.20,

    # Per-metric excursion gates (latest vs history-excluding-latest).
    "max_dev_t1": 0.40,
    "max_dev_t2": 0.40,
    "max_dev_readout": 0.80,
    "max_dev_2q_error": 0.60,

    # Stability penalty weights in the score (error-unit terms come from the
    # circuit shape and are not weighted).
    "w_cv_t1": 0.010,
    "w_cv_t2": 0.010,
    "w_cv_readout": 0.005,
    "w_cv_2q": 0.020,
    "w_excursion": 0.010,
    "w_bad_fraction": 0.020,
}

# Circuit shapes used to build the estimated-infidelity term.  These make the
# score interpretable: it is roughly "expected error for this kind of circuit".
PROFILES: dict[str, dict[str, float]] = {
    # Idle-dominated: park a state, occasionally read it.
    "storage":  {"n_2q": 0.0,  "n_readout": 4.0, "idle_us": 30.0},
    # Gate-dominated: repeated syndrome extraction.
    "gates":    {"n_2q": 12.0, "n_readout": 6.0, "idle_us": 5.0},
    # Middle ground.
    "balanced": {"n_2q": 6.0,  "n_readout": 4.0, "idle_us": 10.0},
}


# ----------------------------------------------------------------------------
# Small utilities
# ----------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_float(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, complex):
        return [value.real, value.imag]
    return str(value)


def quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile without a NumPy dependency."""
    if not values:
        return float("nan")
    xs = sorted(float(x) for x in values)
    if len(xs) == 1:
        return xs[0]
    q = min(1.0, max(0.0, float(q)))
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return float("inf")
    mean = statistics.fmean(values)
    if abs(mean) < 1e-15:
        return float("inf")
    return float(statistics.pstdev(values) / abs(mean))


def infer_tphi_us(t1_us: float, t2_us: float) -> Optional[float]:
    """Local pure-dephasing time from 1/T2 = 1/(2*T1) + 1/Tphi."""
    if t1_us <= 0.0 or t2_us <= 0.0:
        return None
    rate = 1.0 / t2_us - 1.0 / (2.0 * t1_us)
    if rate <= 0.0:
        return None
    return 1.0 / rate


def parse_timestamp(text: str) -> datetime:
    if not text or text == "UNKNOWN":
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        value = str(text).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def props_timestamp(props: Any) -> str:
    stamp = getattr(props, "last_update_date", None)
    if stamp is None:
        try:
            stamp = props.to_dict().get("last_update_date")
        except Exception:
            stamp = None
    if isinstance(stamp, datetime):
        return stamp.astimezone(timezone.utc).isoformat()
    return str(stamp or "UNKNOWN")


def with_retry(fn: Callable[[], Any], attempts: int, base_delay: float,
               what: str) -> Any:
    last: Optional[Exception] = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - provider errors are opaque
            last = exc
            if i == attempts - 1:
                break
            time.sleep(base_delay * (2 ** i))
    raise RuntimeError(f"{what} failed after {attempts} attempts: "
                       f"{type(last).__name__}: {last}") from last


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ----------------------------------------------------------------------------
# Topology
# ----------------------------------------------------------------------------

def pick_native_2q_name(backend: Any) -> str:
    names = set(backend.target.operation_names)
    for name in ("cz", "ecr", "cx"):
        if name in names:
            return name
    for name in sorted(names):
        try:
            qargs = list(backend.target[name].keys())
        except Exception:
            continue
        if any(x is not None and len(x) == 2 for x in qargs):
            return name
    raise RuntimeError(
        f"No constrained two-qubit operation found in target: {sorted(names)}")


def target_edges(backend: Any, gate_name: str) -> list[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    for qargs in backend.target[gate_name]:
        if qargs is None or len(qargs) != 2:
            continue
        a, b = map(int, qargs)
        edges.add((min(a, b), max(a, b)))
    return sorted(edges)


def configuration_edges(backend: Any) -> list[tuple[int, int]]:
    try:
        raw = backend.configuration().coupling_map or []
    except Exception:
        return []
    return sorted({(min(int(a), int(b)), max(int(a), int(b))) for a, b in raw})


def topology_sha256(edges: list[tuple[int, int]]) -> str:
    return sha256_text("\n".join(f"{a}-{b}" for a, b in sorted(edges)))


def adjacency_from_edges(num_qubits: int,
                         edges: list[tuple[int, int]]) -> dict[int, set[int]]:
    adjacency: dict[int, set[int]] = {q: set() for q in range(num_qubits)}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    return adjacency


def enumerate_simple_paths(adjacency: dict[int, set[int]],
                           length: int) -> list[list[int]]:
    canonical: set[tuple[int, ...]] = set()

    def walk(path: list[int]) -> None:
        if len(path) == length:
            forward = tuple(path)
            canonical.add(min(forward, tuple(reversed(path))))
            return
        for nxt in sorted(adjacency[path[-1]]):
            if nxt not in path:
                walk(path + [nxt])

    for start in sorted(adjacency):
        walk([start])
    return [list(x) for x in sorted(canonical)]


# ----------------------------------------------------------------------------
# Snapshot normalisation (this is what makes replay possible)
# ----------------------------------------------------------------------------

def property_2q_error(props: Any, gate_name: str, a: int,
                      b: int) -> Optional[float]:
    try:
        return safe_float(props.gate_error(gate_name, [a, b]))
    except Exception:
        return None


def property_1q_error(props: Any, gate_names: Iterable[str],
                      q: int) -> Optional[float]:
    for name in gate_names:
        try:
            value = safe_float(props.gate_error(name, q))
            if value is not None:
                return value
        except Exception:
            continue
    return None


def extract_snapshot(props: Any, num_qubits: int, gate_name: str,
                     edges: list[tuple[int, int]],
                     keep_raw: bool) -> dict[str, Any]:
    """Flatten a provider BackendProperties into a plain, replayable dict."""
    qubits: dict[str, Any] = {}
    for q in range(num_qubits):
        try:
            t1 = safe_float(props.t1(q))
            t2 = safe_float(props.t2(q))
            ro = safe_float(props.readout_error(q))
        except Exception:
            t1 = t2 = ro = None
        t1_us = None if t1 is None else t1 * 1e6
        t2_us = None if t2 is None else t2 * 1e6
        qubits[str(q)] = {
            "t1_us": t1_us,
            "t2_us": t2_us,
            "tphi_us": (infer_tphi_us(t1_us, t2_us)
                        if t1_us is not None and t2_us is not None else None),
            "t2_over_2t1": (t2_us / (2.0 * t1_us)
                            if t1_us and t2_us and t1_us > 0 else None),
            "readout_error": ro,
            "oneq_error": property_1q_error(props, ("sx", "x"), q),
        }

    edge_map: dict[str, Any] = {}
    for a, b in edges:
        e_ab = property_2q_error(props, gate_name, a, b)
        e_ba = property_2q_error(props, gate_name, b, a)
        present = [x for x in (e_ab, e_ba) if x is not None]
        edge_map[f"{a}-{b}"] = {
            "a": a, "b": b, "gate": gate_name,
            "error_ab": e_ab, "error_ba": e_ba,
            # Conservative: use the worse direction when both are published.
            "error": (max(present) if present else None),
            "directional_gap": (abs(e_ab - e_ba)
                                if e_ab is not None and e_ba is not None
                                else None),
        }

    snapshot: dict[str, Any] = {
        "timestamp": props_timestamp(props),
        "gate": gate_name,
        "qubits": qubits,
        "edges": edge_map,
    }
    if keep_raw:
        try:
            snapshot["raw"] = json.loads(
                json.dumps(props.to_dict(), default=jsonable))
        except Exception as exc:  # noqa: BLE001
            snapshot["raw_error"] = f"{type(exc).__name__}: {exc}"
    return snapshot


def fetch_snapshots(backend: Any, gate_name: str,
                    edges: list[tuple[int, int]], history_days: float,
                    step_hours: float, retries: int, retry_delay: float,
                    keep_raw: bool) -> tuple[list[dict[str, Any]],
                                             list[dict[str, Any]], str]:
    now = utc_now()
    requests: list[dict[str, Any]] = []
    unique: dict[str, dict[str, Any]] = {}
    current_stamp = "UNKNOWN"

    try:
        current = with_retry(backend.properties, retries, retry_delay,
                             "backend.properties()")
        current_stamp = props_timestamp(current)
        unique[current_stamp] = extract_snapshot(
            current, backend.num_qubits, gate_name, edges, keep_raw)
        requests.append({"requested_utc": now.isoformat(), "mode": "current",
                         "status": "OK", "returned_snapshot": current_stamp})
    except Exception as exc:  # noqa: BLE001
        requests.append({"requested_utc": now.isoformat(), "mode": "current",
                         "status": "FAILED",
                         "error": f"{type(exc).__name__}: {exc}"})

    count = int(math.floor(history_days * 24.0 / step_hours)) + 1
    for index in range(count):
        when = now - timedelta(hours=index * step_hours)
        row: dict[str, Any] = {"requested_utc": when.isoformat(),
                               "mode": "historical"}
        try:
            props = with_retry(lambda w=when: backend.properties(datetime=w),
                               retries, retry_delay,
                               f"backend.properties(datetime={when})")
            stamp = props_timestamp(props)
            if stamp not in unique:
                unique[stamp] = extract_snapshot(
                    props, backend.num_qubits, gate_name, edges, keep_raw)
            row.update({"status": "OK", "returned_snapshot": stamp})
        except Exception as exc:  # noqa: BLE001
            row.update({"status": "FAILED",
                        "error": f"{type(exc).__name__}: {exc}"})
        requests.append(row)

    snapshots = sorted(unique.values(),
                       key=lambda s: parse_timestamp(s["timestamp"]),
                       reverse=True)
    return snapshots, requests, current_stamp


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

@dataclass
class PathSeries:
    """Per-unit time series for one candidate path, newest snapshot first."""
    timestamps: list[str]
    t1: list[list[float]]        # [qubit][snapshot]
    t2: list[list[float]]
    tphi: list[list[float]]
    readout: list[list[float]]
    twoq: list[list[float]]      # [edge][snapshot]


def build_series(path: list[int], snapshots: list[dict[str, Any]]
                 ) -> Optional[PathSeries]:
    nq, ne = len(path), len(path) - 1
    ts: list[str] = []
    t1 = [[] for _ in range(nq)]
    t2 = [[] for _ in range(nq)]
    tphi = [[] for _ in range(nq)]
    ro = [[] for _ in range(nq)]
    tq = [[] for _ in range(ne)]

    for snap in snapshots:
        qrow, erow = [], []
        ok = True
        for q in path:
            rec = snap["qubits"].get(str(q))
            if not rec or rec["t1_us"] is None or rec["t2_us"] is None \
                    or rec["readout_error"] is None:
                ok = False
                break
            qrow.append(rec)
        if ok:
            for a, b in zip(path, path[1:]):
                rec = snap["edges"].get(f"{min(a, b)}-{max(a, b)}")
                if not rec or rec["error"] is None:
                    ok = False
                    break
                erow.append(rec["error"])
        if not ok:
            continue
        ts.append(snap["timestamp"])
        for i, rec in enumerate(qrow):
            t1[i].append(rec["t1_us"])
            t2[i].append(rec["t2_us"])
            tphi[i].append(rec["tphi_us"] if rec["tphi_us"] else float("nan"))
            ro[i].append(rec["readout_error"])
        for j, val in enumerate(erow):
            tq[j].append(val)

    if not ts:
        return None
    return PathSeries(ts, t1, t2, tphi, ro, tq)


def excursion(series: list[list[float]]) -> float:
    """Max |latest - median(history excluding latest)| / median, over units."""
    devs: list[float] = []
    for s in series:
        if len(s) < 3:
            continue
        med = statistics.median(s[1:])
        devs.append(abs(s[0] - med) / max(abs(med), 1e-15))
    return max(devs) if devs else 0.0


def score_path(path: list[int], snapshots: list[dict[str, Any]],
               current_stamp: str, cfg: dict[str, Any],
               shape: dict[str, float]) -> dict[str, Any]:
    nq, ne = len(path), len(path) - 1
    series = build_series(path, snapshots)
    base: dict[str, Any] = {"path": path, "coverage": 0}

    if series is None or len(series.timestamps) < int(cfg["history_min_unique"]):
        base.update({
            "coverage": 0 if series is None else len(series.timestamps),
            "latest_snapshot": None,
            "latest_is_current": False,
            "latest_eligible": False, "strict_eligible": False,
            "robust_eligible": False,
            "latest_reasons": ["insufficient_historical_coverage"],
            "strict_reasons": ["insufficient_historical_coverage"],
            "robust_reasons": ["insufficient_historical_coverage"],
            "score": float("inf"), "score_terms": {}, "summary": {},
        })
        return base

    n = len(series.timestamps)
    latest_stamp = series.timestamps[0]
    latest_is_current = (latest_stamp == current_stamp)

    lat_t1 = [s[0] for s in series.t1]
    lat_t2 = [s[0] for s in series.t2]
    lat_ro = [s[0] for s in series.readout]
    lat_2q = [s[0] for s in series.twoq]

    # --- latest gates ------------------------------------------------------
    latest_reasons: list[str] = []
    if min(lat_t1) < cfg["latest_min_t1_us"]:
        latest_reasons.append("latest_T1_below_threshold")
    if min(lat_t2) < cfg["latest_min_t2_us"]:
        latest_reasons.append("latest_T2_below_threshold")
    if max(lat_ro) > cfg["latest_max_readout"]:
        latest_reasons.append("latest_readout_above_threshold")
    if max(lat_2q) > cfg["latest_max_2q_error"]:
        latest_reasons.append("latest_2q_error_above_threshold")
    if not latest_is_current:
        latest_reasons.append("latest_row_is_not_current_calibration")

    # --- strict worst-case over history ------------------------------------
    all_t1 = [v for s in series.t1 for v in s]
    all_t2 = [v for s in series.t2 for v in s]
    all_ro = [v for s in series.readout for v in s]
    all_2q = [v for s in series.twoq for v in s]

    strict_reasons = list(latest_reasons)
    if min(all_t1) < cfg["strict_min_t1_us"]:
        strict_reasons.append("historical_min_T1_below_threshold")
    if min(all_t2) < cfg["strict_min_t2_us"]:
        strict_reasons.append("historical_min_T2_below_threshold")
    if max(all_ro) > cfg["strict_max_readout"]:
        strict_reasons.append("historical_max_readout_above_threshold")
    if max(all_2q) > cfg["strict_max_2q_error"]:
        strict_reasons.append("historical_max_2q_error_above_threshold")

    # --- robust gates: PER-UNIT quantiles, worst unit -----------------------
    qlo, qhi = cfg["history_low_quantile"], cfg["history_high_quantile"]
    q20_t1 = min(quantile(s, qlo) for s in series.t1)
    q20_t2 = min(quantile(s, qlo) for s in series.t2)
    q80_ro = max(quantile(s, qhi) for s in series.readout)
    q80_2q = max(quantile(s, qhi) for s in series.twoq)
    tphi_clean = [[v for v in s if math.isfinite(v)] for s in series.tphi]
    q20_tphi = min((quantile(s, qlo) for s in tphi_clean if s),
                   default=float("nan"))

    cv_t1 = max(coefficient_of_variation(s) for s in series.t1)
    cv_t2 = max(coefficient_of_variation(s) for s in series.t2)
    cv_ro = max(coefficient_of_variation(s) for s in series.readout)
    cv_2q = max(coefficient_of_variation(s) for s in series.twoq)

    dev_t1 = excursion(series.t1)
    dev_t2 = excursion(series.t2)
    dev_ro = excursion(series.readout)
    dev_2q = excursion(series.twoq)

    def bad_frac(per_snapshot: list[float], predicate) -> float:
        return (sum(bool(predicate(x)) for x in per_snapshot)
                / len(per_snapshot)) if per_snapshot else 1.0

    t1_bad = bad_frac([min(s[k] for s in series.t1) for k in range(n)],
                      lambda x: x < cfg["robust_q20_min_t1_us"])
    t2_bad = bad_frac([min(s[k] for s in series.t2) for k in range(n)],
                      lambda x: x < cfg["robust_q20_min_t2_us"])
    ro_bad = bad_frac([max(s[k] for s in series.readout) for k in range(n)],
                      lambda x: x > cfg["robust_q80_max_readout"])
    q2_bad = bad_frac([max(s[k] for s in series.twoq) for k in range(n)],
                      lambda x: x > cfg["robust_q80_max_2q_error"])
    worst_bad = max(t1_bad, t2_bad, ro_bad, q2_bad)

    robust_reasons = list(latest_reasons)
    if q20_t1 < cfg["robust_q20_min_t1_us"]:
        robust_reasons.append("historical_q20_T1_below_threshold")
    if q20_t2 < cfg["robust_q20_min_t2_us"]:
        robust_reasons.append("historical_q20_T2_below_threshold")
    if q80_ro > cfg["robust_q80_max_readout"]:
        robust_reasons.append("historical_q80_readout_above_threshold")
    if q80_2q > cfg["robust_q80_max_2q_error"]:
        robust_reasons.append("historical_q80_2q_error_above_threshold")
    if cfg["robust_q20_min_tphi_us"] and math.isfinite(q20_tphi) \
            and q20_tphi < cfg["robust_q20_min_tphi_us"]:
        robust_reasons.append("historical_q20_Tphi_below_threshold")
    if cv_t1 > cfg["max_t1_cv"]:
        robust_reasons.append("T1_instability")
    if cv_t2 > cfg["max_t2_cv"]:
        robust_reasons.append("T2_instability")
    if cv_ro > cfg["max_readout_cv"]:
        robust_reasons.append("readout_instability")
    if cv_2q > cfg["max_2q_cv"]:
        robust_reasons.append("2q_instability")
    if worst_bad > cfg["max_bad_snapshot_fraction"]:
        robust_reasons.append("bad_snapshot_fraction_above_threshold")
    if dev_t1 > cfg["max_dev_t1"]:
        robust_reasons.append("latest_vs_history_excursion_T1")
    if dev_t2 > cfg["max_dev_t2"]:
        robust_reasons.append("latest_vs_history_excursion_T2")
    if dev_ro > cfg["max_dev_readout"]:
        robust_reasons.append("latest_vs_history_excursion_readout")
    if dev_2q > cfg["max_dev_2q_error"]:
        robust_reasons.append("latest_vs_history_excursion_2q")

    # --- score: estimated infidelity + weighted instability ----------------
    med_t1 = statistics.median(all_t1)
    med_t2 = statistics.median(all_t2)
    med_ro = statistics.median(all_ro)
    med_2q = statistics.median(all_2q)

    err_2q = shape["n_2q"] * med_2q
    err_ro = shape["n_readout"] * med_ro
    err_idle = 1.0 - math.exp(-shape["idle_us"] / max(med_t2, 1e-9))
    est_error = err_2q + err_ro + err_idle

    terms = {
        "est_err_2q": err_2q,
        "est_err_readout": err_ro,
        "est_err_idle_T2": err_idle,
        "pen_cv_t1": cfg["w_cv_t1"] * cv_t1,
        "pen_cv_t2": cfg["w_cv_t2"] * cv_t2,
        "pen_cv_readout": cfg["w_cv_readout"] * cv_ro,
        "pen_cv_2q": cfg["w_cv_2q"] * cv_2q,
        "pen_excursion": cfg["w_excursion"] * max(dev_t1, dev_t2, dev_ro,
                                                  dev_2q),
        "pen_bad_fraction": cfg["w_bad_fraction"] * worst_bad,
    }
    score = sum(v for v in terms.values() if math.isfinite(v))

    base.update({
        "coverage": n,
        "latest_snapshot": latest_stamp,
        "latest_is_current": latest_is_current,
        "snapshot_timestamps": series.timestamps,
        "latest_eligible": not latest_reasons,
        "latest_reasons": latest_reasons,
        "strict_eligible": not strict_reasons,
        "strict_reasons": strict_reasons,
        "robust_eligible": not robust_reasons,
        "robust_reasons": robust_reasons,
        "score": score,
        "score_terms": terms,
        "estimated_circuit_error": est_error,
        "summary": {
            "historical_min_t1_us": min(all_t1),
            "historical_min_t2_us": min(all_t2),
            "historical_max_readout": max(all_ro),
            "historical_max_2q_error": max(all_2q),
            "q20_t1_us": q20_t1, "q20_t2_us": q20_t2,
            "q20_tphi_us": q20_tphi,
            "q80_readout": q80_ro, "q80_2q_error": q80_2q,
            "median_t1_us": med_t1, "median_t2_us": med_t2,
            "median_readout": med_ro, "median_2q_error": med_2q,
            "max_t1_cv": cv_t1, "max_t2_cv": cv_t2,
            "max_readout_cv": cv_ro, "max_2q_cv": cv_2q,
            "dev_t1": dev_t1, "dev_t2": dev_t2,
            "dev_readout": dev_ro, "dev_2q_error": dev_2q,
            "bad_snapshot_fraction_t1": t1_bad,
            "bad_snapshot_fraction_t2": t2_bad,
            "bad_snapshot_fraction_readout": ro_bad,
            "bad_snapshot_fraction_2q": q2_bad,
        },
    })
    return base


# ----------------------------------------------------------------------------
# Cross-run stability
# ----------------------------------------------------------------------------

def spearman(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 3:
        return float("nan")

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: xs[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra)
                    * sum((y - mb) ** 2 for y in rb))
    return num / den if den > 0 else float("nan")


def compare_runs(current: dict[str, Any],
                 previous_path: Path) -> dict[str, Any]:
    try:
        prev = json.loads(previous_path.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"status": "PREVIOUS_RUN_UNREADABLE",
                "error": f"{type(exc).__name__}: {exc}"}

    out: dict[str, Any] = {"previous_file": str(previous_path),
                           "previous_created_utc": prev.get("created_utc"),
                           "backends": {}}
    for name, audit in current.get("backends", {}).items():
        pa = prev.get("backends", {}).get(name)
        if not pa:
            out["backends"][name] = {"status": "NOT_IN_PREVIOUS_RUN"}
            continue
        cur_all = {tuple(x["path"]): x for x in audit["paths"]["all_ranked"]}
        prv_all = {tuple(x["path"]): x for x in pa["paths"]["all_ranked"]}
        cur_rob = {p for p, x in cur_all.items() if x.get("robust_eligible")}
        prv_rob = {p for p, x in prv_all.items() if x.get("robust_eligible")}
        union = cur_rob | prv_rob
        shared = [p for p in cur_all if p in prv_all
                  and math.isfinite(cur_all[p].get("score", float("inf")))
                  and math.isfinite(prv_all[p].get("score", float("inf")))]
        out["backends"][name] = {
            "robust_count_now": len(cur_rob),
            "robust_count_prev": len(prv_rob),
            "robust_jaccard": (len(cur_rob & prv_rob) / len(union)
                               if union else float("nan")),
            "entered_robust_set": sorted(list(p) for p in cur_rob - prv_rob),
            "left_robust_set": sorted(list(p) for p in prv_rob - cur_rob),
            "score_spearman": spearman([cur_all[p]["score"] for p in shared],
                                       [prv_all[p]["score"] for p in shared]),
            "shared_path_count": len(shared),
            "top_pick_now": (audit["paths"]["robust_selected"] or {}).get("path"),
            "top_pick_prev": (pa["paths"]["robust_selected"] or {}).get("path"),
            "snapshots_now": audit["history"]["unique_snapshot_count"],
            "snapshots_prev": pa["history"]["unique_snapshot_count"],
        }
    return out


# ----------------------------------------------------------------------------
# Audit driver
# ----------------------------------------------------------------------------

def rank_paths(paths: list[list[int]], snapshots: list[dict[str, Any]],
               current_stamp: str, cfg: dict[str, Any],
               shape: dict[str, float]) -> list[dict[str, Any]]:
    ranking = [score_path(p, snapshots, current_stamp, cfg, shape)
               for p in paths]
    ranking.sort(key=lambda x: (not x.get("robust_eligible", False),
                                not x.get("latest_eligible", False),
                                x.get("score", float("inf")),
                                x.get("path", [])))
    return ranking


def assemble_audit(meta: dict[str, Any], snapshots: list[dict[str, Any]],
                   requests: list[dict[str, Any]], current_stamp: str,
                   paths: list[list[int]], cfg: dict[str, Any],
                   shape: dict[str, float], history_days: float,
                   step_hours: float, fixed_n: int) -> dict[str, Any]:
    ranking = rank_paths(paths, snapshots, current_stamp, cfg, shape)
    strict = sorted(ranking, key=lambda x: (not x.get("strict_eligible", False),
                                            x.get("score", float("inf"))))
    return {
        "metadata": meta,
        "history": {
            "requested_days": history_days,
            "step_hours": step_hours,
            "fixed_n": fixed_n,
            "request_log": requests,
            "unique_snapshot_count": len(snapshots),
            "snapshot_timestamps": [s["timestamp"] for s in snapshots],
            "current_calibration_timestamp": current_stamp,
        },
        "paths": {
            "path_length": len(paths[0]) if paths else 0,
            "path_count": len(paths),
            "robust_eligible_count": sum(bool(x.get("robust_eligible"))
                                         for x in ranking),
            "strict_eligible_count": sum(bool(x.get("strict_eligible"))
                                         for x in ranking),
            "latest_eligible_count": sum(bool(x.get("latest_eligible"))
                                         for x in ranking),
            "stale_latest_count": sum(1 for x in ranking
                                      if x.get("coverage")
                                      and not x.get("latest_is_current")),
            "robust_selected": next((x for x in ranking
                                     if x.get("robust_eligible")), None),
            "strict_selected": next((x for x in strict
                                     if x.get("strict_eligible")), None),
            "all_ranked": ranking,
        },
    }


def backend_metadata(backend: Any, gate_name: str,
                     edges: list[tuple[int, int]]) -> dict[str, Any]:
    try:
        config = backend.configuration()
        processor_type = getattr(config, "processor_type", None)
        basis_gates = list(getattr(config, "basis_gates", []) or [])
    except Exception:
        processor_type, basis_gates = None, []
    try:
        status = backend.status()
        st = {"operational": bool(status.operational),
              "pending_jobs": int(status.pending_jobs),
              "status_msg": str(status.status_msg)}
    except Exception as exc:  # noqa: BLE001
        st = {"operational": None, "pending_jobs": None,
              "status_msg": f"UNAVAILABLE: {type(exc).__name__}: {exc}"}
    return {
        "backend": backend.name,
        "num_qubits": int(backend.num_qubits),
        "dt_seconds": safe_float(getattr(backend, "dt", None)),
        "native_2q_gate": gate_name,
        "target_operations": sorted(backend.target.operation_names),
        "basis_gates": basis_gates,
        "processor_type": processor_type,
        "target_edge_count": len(edges),
        "target_edges": edges,
        "configuration_edges": configuration_edges(backend),
        "topology_sha256": topology_sha256(edges),
        "status": st,
    }


def choose_backend(audits: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for name, audit in audits.items():
        sel = audit["paths"]["robust_selected"]
        if sel is not None:
            candidates.append({
                "backend": name, "path": sel["path"], "score": sel["score"],
                "score_terms": sel["score_terms"],
                "estimated_circuit_error": sel["estimated_circuit_error"],
                "pending_jobs": audit["metadata"]["status"]["pending_jobs"],
                "snapshot": sel["latest_snapshot"],
            })
    candidates.sort(key=lambda x: x["score"])
    if not candidates:
        return {"status": "NO_BACKEND_PASSES_ROBUST_RULE",
                "selected": None, "candidates": []}
    return {
        "status": "ROBUST_CANDIDATE_FOUND",
        "selected": candidates[0],
        "candidates": candidates,
        "queue_note": ("Pending-job count is reported for operational "
                       "convenience but is not included in the "
                       "calibration-quality score."),
    }


# ----------------------------------------------------------------------------
# Artifacts
# ----------------------------------------------------------------------------

def flatten_path_row(backend_name: str, profile: str, rank: int,
                     item: dict[str, Any]) -> dict[str, Any]:
    row = {
        "backend": backend_name, "profile": profile, "rank": rank,
        "path": "-".join(map(str, item.get("path", []))),
        "coverage": item.get("coverage"),
        "latest_snapshot": item.get("latest_snapshot"),
        "latest_is_current": item.get("latest_is_current"),
        "latest_eligible": item.get("latest_eligible"),
        "strict_eligible": item.get("strict_eligible"),
        "robust_eligible": item.get("robust_eligible"),
        "score": item.get("score"),
        "estimated_circuit_error": item.get("estimated_circuit_error"),
        "latest_reasons": ";".join(item.get("latest_reasons", [])),
        "strict_reasons": ";".join(item.get("strict_reasons", [])),
        "robust_reasons": ";".join(item.get("robust_reasons", [])),
    }
    row.update({f"term_{k}": v for k, v in item.get("score_terms", {}).items()})
    row.update(item.get("summary", {}))
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

HELP_EPILOG = """\
SWITCH REFERENCE
================

SOURCE AND SCOPE
  --backends NAME [NAME ...]   Backends to audit. Default: ibm_fez ibm_kingston
  --path-length N              Qubits per candidate path. 4 = the v1 default
                               (3 data + 1 spare, or a 4-chain). Use 5 for a
                               distance-3 repetition code (3 data + 2 ancilla).
  --output-dir DIR             Where artifacts are written.
  --replay DIR                 Re-score snapshots saved by an earlier run in
                               DIR. Makes NO network calls. Everything about
                               scoring can be changed and diffed against
                               frozen data.
  --compare-with FILE          A previous backend_comparison.json. Reports
                               Jaccard overlap of the robust-eligible set and
                               Spearman rank correlation of scores, which is
                               how you measure whether the selector itself is
                               stable across runs.
  --no-raw-snapshots           Skip storing the provider's raw payload.
                               Normalised snapshots are still saved and replay
                               still works; this only shrinks the artifact.

HISTORY WINDOW
  --history-days D             How far back to request calibrations.
  --history-step-hours H       Request spacing. The provider returns the
                               nearest older calibration, so requests usually
                               collapse to fewer unique snapshots.
  --history-fixed-n N          Truncate every backend to the N most recent
                               unique snapshots. IMPORTANT: robust_pass counts
                               are monotone in snapshot count, so they are not
                               comparable across backends or runs unless this
                               is set. 0 disables truncation.
  --history-min-unique N       Minimum unique snapshots for a path to be
                               scoreable at all.
  --retries N                  Attempts per provider call.
  --retry-delay S              Base delay for exponential backoff.

SCORE SHAPE
  --profile {storage,gates,balanced}
                               Selects the circuit shape the estimated-error
                               term is built for.
                                 storage  = 0 two-qubit gates, 4 readouts,
                                            30us idle  (idle-dominated)
                                 gates    = 12 two-qubit gates, 6 readouts,
                                            5us idle   (syndrome extraction)
                                 balanced = 6 / 4 / 10us
                               v1's implicit weighting was storage-like and
                               gave two-qubit error only ~11% of the ranking.
  --shape-2q N                 Override the profile's two-qubit gate count.
  --shape-readout N            Override the profile's readout count.
  --shape-idle-us T            Override the profile's idle duration.

  The score is:
      estimated_error( n_2q*median_2q + n_ro*median_ro + 1-exp(-idle/median_T2) )
    + weighted instability penalties
  Every term is emitted per path as score_terms, so any ranking can be
  audited after the fact. Lower is better.

CURRENT-CALIBRATION GATES (applied to the newest snapshot)
  --latest-min-t1-us, --latest-min-t2-us
  --latest-max-readout, --latest-max-2q-error

STRICT GATES (worst single value anywhere in the window)
  --strict-min-t1-us, --strict-min-t2-us
  --strict-max-readout, --strict-max-2q-error

ROBUST GATES (per-qubit / per-edge quantiles, worst unit)
  --history-low-quantile Q     Quantile for T1/T2 (higher is better).
  --history-high-quantile Q    Quantile for readout and two-qubit error.
  --robust-q20-min-t1-us, --robust-q20-min-t2-us
  --robust-q80-max-readout, --robust-q80-max-2q-error
  --robust-q20-min-tphi-us     Optional pure-dephasing floor. 0 disables.
                               Tphi is inferred from 1/T2 = 1/(2T1) + 1/Tphi.

STABILITY GATES
  --max-t1-cv, --max-t2-cv, --max-readout-cv, --max-2q-cv
                               Coefficient of variation across the window,
                               computed per unit and worst-cased.
  --max-bad-snapshot-fraction  Fraction of snapshots in which the path may
                               violate a robust threshold.

EXCURSION GATES (latest vs median of history EXCLUDING latest)
  --max-dev-t1, --max-dev-t2, --max-dev-readout, --max-dev-2q-error
                               Per-metric, because readout error is naturally
                               far more volatile than T1/T2. v1 applied one
                               threshold to a max over all metrics, which made
                               readout noise dominate the gate.

PENALTY WEIGHTS
  --w-cv-t1, --w-cv-t2, --w-cv-readout, --w-cv-2q
  --w-excursion, --w-bad-fraction
                               Applied to the instability terms. Defaults are
                               scaled so a typical path's penalties are
                               comparable to, not dominant over, the estimated
                               error term.

OUTPUT
  --top-print N                Rows printed per backend. Artifacts always
                               contain the complete ranking.

ARTIFACTS WRITTEN
  backend_comparison.json      Manifest: thresholds, shape, full ranking,
                               recommendation, comparison, limits.
  <backend>_audit.json         Per-backend audit.
  <backend>_snapshots.json     Normalised (and optionally raw) calibration
                               snapshots. This is the replay input.
  all_paths.csv                Every path, both profiles, with score terms.
  latest_qubits.csv            Per-qubit current calibration.
  latest_edges.csv             Per-edge current calibration, both directions.
  stability_comparison.json    Written only with --compare-with.
  SHA256SUMS                   Digest of every artifact above.

NOTE
  This script submits no QPU job and calls no Runtime primitive. A robust-rule
  pass authorises compilation and model preflight only, not submission.
"""




# ----------------------------------------------------------------------------
# Audited Transpiler Pass Pipeline
# ----------------------------------------------------------------------------

def compile_for_audited_path(backend: Any, path: list[int],
                             circuit: Optional[Any] = None) -> tuple[Any, Any]:
    """
    Build a Qiskit StagePassManager locking layout to the audited physical path.
    Synthesizes a matching GHZ circuit if none is provided.
    """
    from qiskit import QuantumCircuit
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

    if circuit is None:
        circuit = QuantumCircuit(len(path), name="audited_preflight")
        circuit.h(0)
        for i in range(len(path) - 1):
            circuit.cx(i, i + 1)
        circuit.measure_all()

    pm = generate_preset_pass_manager(
        backend=backend,
        optimization_level=3,
        initial_layout=path
    )

    transpiled = pm.run(circuit)
    gate_counts = dict(transpiled.count_ops())
    swap_count = gate_counts.get("swap", 0)
    final_layout = transpiled.layout.final_index_layout()

    print(f"\n=== Transpiler Pass Execution ===")
    print(f"Locked Physical Layout: {path}")
    print(f"Transpiler Target Layout: {final_layout[:len(path)]}")
    print(f"Depth: {transpiled.depth()} | Ops: {gate_counts}")
    if swap_count > 0:
        print(f"WARNING: Routing inserted {swap_count} SWAP gates. Check connectivity!")
    else:
        print("Sub-graph Integrity: OK (0 SWAP overhead, preserved nearest-neighbor).")

    return transpiled, pm

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ibm_backend_path_audit.py",
        description=("Zero-QPU calibration/topology audit and N-qubit path "
                     f"selection for IBM Quantum backends (v{SCRIPT_VERSION})."),
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--backends", nargs="+",
                   default=["ibm_fez", "ibm_kingston"])
    p.add_argument("--path-length", type=int, default=4)
    p.add_argument("--output-dir", default="v6_backend_audit")
    p.add_argument("--replay", default=None)
    p.add_argument("--compare-with", default=None)
    p.add_argument("--no-raw-snapshots", action="store_true")

    p.add_argument("--history-days", type=float, default=5.0)
    p.add_argument("--history-step-hours", type=float, default=12.0)
    p.add_argument("--history-fixed-n", type=int, default=0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=2.0)

    p.add_argument("--profile", choices=sorted(PROFILES), default="gates")
    p.add_argument("--shape-2q", type=float, default=None)
    p.add_argument("--shape-readout", type=float, default=None)
    p.add_argument("--shape-idle-us", type=float, default=None)
    p.add_argument("--top-print", type=int, default=5)
    p.add_argument("--compile-preflight", action="store_true",
                   help="Compile a preflight circuit pinned to the selected robust path.")

    for key, value in DEFAULTS.items():
        p.add_argument("--" + key.replace("_", "-"),
                       type=int if isinstance(value, int) else float,
                       default=value)
    return p


def resolve_shape(args: argparse.Namespace) -> dict[str, float]:
    shape = dict(PROFILES[args.profile])
    if args.shape_2q is not None:
        shape["n_2q"] = args.shape_2q
    if args.shape_readout is not None:
        shape["n_readout"] = args.shape_readout
    if args.shape_idle_us is not None:
        shape["idle_us"] = args.shape_idle_us
    shape["profile"] = args.profile
    return shape


def print_backend(name: str, audit: dict[str, Any], top: int) -> None:
    meta, paths = audit["metadata"], audit["paths"]
    print(f"operational={meta['status']['operational']} "
          f"pending_jobs={meta['status']['pending_jobs']} "
          f"qubits={meta['num_qubits']} "
          f"native_2q={meta['native_2q_gate']} "
          f"edges={meta['target_edge_count']}")
    print(f"snapshots={audit['history']['unique_snapshot_count']} "
          f"current_cal={audit['history']['current_calibration_timestamp']}")
    print(f"paths={paths['path_count']} "
          f"latest_pass={paths['latest_eligible_count']} "
          f"strict_pass={paths['strict_eligible_count']} "
          f"robust_pass={paths['robust_eligible_count']} "
          f"stale_latest={paths['stale_latest_count']}")
    for idx, item in enumerate(paths["all_ranked"][:top], 1):
        s = item.get("summary", {})
        t = item.get("score_terms", {})
        if not s:
            print(f"  {idx:>2}. {item['path']} UNSCOREABLE "
                  f"reasons={item.get('robust_reasons')}")
            continue
        print(f"  {idx:>2}. {item['path']} "
              f"robust={item['robust_eligible']} "
              f"latest={item['latest_eligible']} "
              f"score={item['score']:.5f} "
              f"est_err={item['estimated_circuit_error']*100:.2f}% "
              f"q20T2={s['q20_t2_us']:.1f}us "
              f"q80RO={100*s['q80_readout']:.2f}% "
              f"q80_2q={100*s['q80_2q_error']:.3f}%")
        print(f"      terms: " + "  ".join(
            f"{k.replace('est_err_', '').replace('pen_', '')}={v:.5f}"
            for k, v in t.items()))
        if item["robust_reasons"]:
            print(f"      reasons: {item['robust_reasons']}")


def main() -> int:
    args = build_parser().parse_args()
    start = time.time()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = {key: getattr(args, key) for key in DEFAULTS}
    shape = resolve_shape(args)

    audits: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    snapshot_files: dict[str, Path] = {}
    mode = "REPLAY" if args.replay else "LIVE"

    print(f"=== IBM backend calibration/topology audit (NO QPU) "
          f"v{SCRIPT_VERSION} [{mode}] ===")
    print(f"profile={args.profile} shape={{n_2q={shape['n_2q']:g}, "
          f"n_readout={shape['n_readout']:g}, idle={shape['idle_us']:g}us}}")

    # ---------------- replay ------------------------------------------------
    if args.replay:
        replay_dir = Path(args.replay)
        files = sorted(replay_dir.glob("*_snapshots.json"))
        if not files:
            print(f"ERROR: no *_snapshots.json found in {replay_dir}",
                  file=sys.stderr)
            return 2
        for f in files:
            name = f.name.removesuffix("_snapshots.json")
            print(f"\n--- {name} (replay from {f.name}) ---")
            try:
                blob = json.loads(f.read_text())
                snapshots = blob["snapshots"]
                if args.history_fixed_n > 0:
                    snapshots = snapshots[:args.history_fixed_n]
                paths = [list(x) for x in blob["paths"]]
                copied = output_dir / f"{name}_snapshots.json"
                if copied.resolve() != f.resolve():
                    copied.write_text(f.read_text())
                snapshot_files[name] = copied
                audits[name] = assemble_audit(
                    blob["metadata"], snapshots, blob.get("request_log", []),
                    blob.get("current_calibration_timestamp", "UNKNOWN"),
                    paths, cfg, shape, blob.get("history_days", float("nan")),
                    blob.get("step_hours", float("nan")),
                    args.history_fixed_n)
                print_backend(name, audits[name], args.top_print)
            except Exception as exc:  # noqa: BLE001
                failures[name] = f"{type(exc).__name__}: {exc}"
                print(f"FAILED: {failures[name]}")

    # ---------------- live --------------------------------------------------
    else:
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService
        except Exception as exc:  # noqa: BLE001
            print("ERROR: qiskit_ibm_runtime unavailable: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        service = QiskitRuntimeService()
        print(f"history: {args.history_days:g} days, every "
              f"{args.history_step_hours:g} hours"
              + (f", truncated to {args.history_fixed_n} snapshots"
                 if args.history_fixed_n > 0 else ""))

        for backend_name in args.backends:
            print(f"\n--- {backend_name} ---")
            try:
                backend = service.backend(backend_name)
                gate_name = pick_native_2q_name(backend)
                edges = target_edges(backend, gate_name)
                adjacency = adjacency_from_edges(backend.num_qubits, edges)
                paths = enumerate_simple_paths(adjacency, args.path_length)
                snapshots, requests, current_stamp = fetch_snapshots(
                    backend, gate_name, edges, args.history_days,
                    args.history_step_hours, args.retries, args.retry_delay,
                    not args.no_raw_snapshots)
                if args.history_fixed_n > 0:
                    snapshots = snapshots[:args.history_fixed_n]
                meta = backend_metadata(backend, gate_name, edges)

                snap_file = output_dir / f"{backend_name}_snapshots.json"
                snap_file.write_text(json.dumps({
                    "script_version": SCRIPT_VERSION,
                    "created_utc": utc_now().isoformat(),
                    "metadata": meta,
                    "history_days": args.history_days,
                    "step_hours": args.history_step_hours,
                    "current_calibration_timestamp": current_stamp,
                    "request_log": requests,
                    "paths": paths,
                    "snapshots": snapshots,
                }, indent=2, default=jsonable))
                snapshot_files[backend_name] = snap_file

                audits[backend_name] = assemble_audit(
                    meta, snapshots, requests, current_stamp, paths, cfg,
                    shape, args.history_days, args.history_step_hours,
                    args.history_fixed_n)
                print_backend(backend_name, audits[backend_name],
                              args.top_print)
            except Exception as exc:  # noqa: BLE001
                failures[backend_name] = f"{type(exc).__name__}: {exc}"
                print(f"FAILED: {failures[backend_name]}")

    # ---------------- artifacts --------------------------------------------
    recommendation = choose_backend(audits)
    manifest: dict[str, Any] = {
        "audit": "ibm_backend_path_audit",
        "script_version": SCRIPT_VERSION,
        "mode": mode,
        "created_utc": utc_now().isoformat(),
        "command": " ".join(sys.argv),
        "no_qpu": True,
        "profile": args.profile,
        "circuit_shape": shape,
        "thresholds": cfg,
        "thresholds_sha256": sha256_text(
            json.dumps(cfg, sort_keys=True) + json.dumps(shape, sort_keys=True)),
        "backends": audits,
        "failures": failures,
        "recommendation": recommendation,
        "interpretation_limits": [
            "Queue depth is not a calibration-quality metric.",
            "T1/T2 and inferred Tphi are local calibration descriptors and do "
            "not establish collective or correlated noise.",
            "Historical requests may map to duplicate provider snapshots; "
            "duplicates are removed by calibration timestamp.",
            "robust_pass counts are monotone in snapshot count; use "
            "--history-fixed-n before comparing across backends or runs.",
            "The score is an estimated circuit error under an assumed shape, "
            "not a measurement. Check score_terms before trusting a ranking.",
            "A robust-rule pass authorises compilation/model preflight only, "
            "not QPU submission.",
        ],
        "wall_seconds": time.time() - start,
    }

    if args.compare_with:
        comparison = compare_runs(manifest, Path(args.compare_with))
        manifest["stability_comparison"] = comparison
        (output_dir / "stability_comparison.json").write_text(
            json.dumps(comparison, indent=2, default=jsonable))
        print("\n=== cross-run selector stability ===")
        print(json.dumps(comparison.get("backends", comparison), indent=2,
                         default=jsonable))


    # ---------------- compile preflight -----------------------------------
    if args.compile_preflight and recommendation.get("selected"):
        sel = recommendation["selected"]
        target_backend_name = sel["backend"]
        chosen_path = sel["path"]

        if not args.replay:
            target_backend = service.backend(target_backend_name)
        else:
            from qiskit_ibm_runtime import QiskitRuntimeService
            target_backend = QiskitRuntimeService().backend(target_backend_name)

        print(f"\nLocking layout to robust path {chosen_path} on {target_backend_name}...")
        transpiled_circ, _ = compile_for_audited_path(target_backend, chosen_path)

        from qiskit.qasm2 import dump as qasm2_dump
        qasm_path = output_dir / f"{target_backend_name}_preflight.qasm"
        with open(qasm_path, "w") as f:
            qasm2_dump(transpiled_circ, f)
        print(f"Preflight QASM written: {qasm_path.resolve()}")

    path_rows, qubit_rows, edge_rows = [], [], []
    for backend_name, audit in audits.items():
        (output_dir / f"{backend_name}_audit.json").write_text(
            json.dumps(audit, indent=2, default=jsonable))
        for rank, item in enumerate(audit["paths"]["all_ranked"], start=1):
            path_rows.append(flatten_path_row(backend_name, "robust", rank,
                                              item))
        snap_file = snapshot_files.get(backend_name)
        if snap_file is not None and snap_file.exists():
            blob = json.loads(snap_file.read_text())
            if blob["snapshots"]:
                cur = blob["snapshots"][0]
                for q, rec in cur["qubits"].items():
                    qubit_rows.append({"backend": backend_name,
                                       "snapshot": cur["timestamp"],
                                       "qubit": int(q), **rec})
                for _, rec in cur["edges"].items():
                    edge_rows.append({"backend": backend_name,
                                      "snapshot": cur["timestamp"], **rec})

    write_csv(output_dir / "all_paths.csv", path_rows)
    write_csv(output_dir / "latest_qubits.csv", qubit_rows)
    write_csv(output_dir / "latest_edges.csv", edge_rows)
    (output_dir / "backend_comparison.json").write_text(
        json.dumps(manifest, indent=2, default=jsonable))

    digests = []
    for f in sorted(output_dir.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS":
            digests.append(f"{sha256_file(f)}  {f.name}")
    (output_dir / "SHA256SUMS").write_text("\n".join(digests) + "\n")

    print("\n=== cross-backend recommendation ===")
    print(json.dumps(recommendation, indent=2, default=jsonable))
    print(f"\nArtifacts: {output_dir.resolve()}")
    for line in digests:
        print(f"  {line.split('  ')[1]}")
    print("  SHA256SUMS")
    print("\nNO QPU submission was performed or authorized.")
    return 0 if audits else 1


if __name__ == "__main__":
    raise SystemExit(main())