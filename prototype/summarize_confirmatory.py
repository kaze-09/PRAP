#!/usr/bin/env python3
"""Reprint official confirmatory tables from unified_seed_rerun/ (no API key)."""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

HERE = Path(__file__).resolve().parent
RUN = HERE / "unified_seed_rerun"

SCALE_ARMS = (
    ("leaseonly", "Lease-only"),
    ("prap", "Write gating"),
    ("orch", "Orchestrator"),
    ("noproto", "No protocol"),
    ("promptfix", "Prompt"),
)
T2C4_ARMS = (
    ("leaseonly", "Lease-only"),
    ("noproto", "No protocol"),
    ("promptfix", "Prompt"),
)


def load_trials(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} is not a trial list")
    return data


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _comb(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def fisher_two_sided(a: int, n1: int, b: int, n2: int) -> float:
    """Two-sided Fisher exact test on two binomial counts."""
    # Table: [[a, n1-a], [b, n2-b]]
    x11, x12, x21, x22 = a, n1 - a, b, n2 - b
    row1, row2 = x11 + x12, x21 + x22
    col1, col2 = x11 + x21, x12 + x22
    n = row1 + row2
    lo = max(0, col1 - row2)
    hi = min(col1, row1)

    def p_table(x: int) -> float:
        return (
            _comb(row1, x)
            * _comb(row2, col1 - x)
            / _comb(n, col1)
        )

    p_obs = p_table(x11)
    total = 0.0
    for x in range(lo, hi + 1):
        p = p_table(x)
        if p <= p_obs + 1e-15:
            total += p
    return min(1.0, total)


def mean_sd(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return (float("nan"), float("nan"))
    if len(xs) == 1:
        return (xs[0], 0.0)
    return (mean(xs), stdev(xs))


def injection_key(trial: dict) -> tuple:
    agents = trial.get("agents") or []
    rows = []
    for ag in agents:
        override = ag.get("override")
        if isinstance(override, dict):
            ov = tuple(sorted(override.items()))
        else:
            ov = override
        rows.append((ag.get("id") or ag.get("agent"), bool(ag.get("injected")), bool(ag.get("effective")), ov))
    return (trial.get("seed"), tuple(rows))


def summarize_scale() -> None:
    arms = {}
    for key, label in SCALE_ARMS:
        path = RUN / f"scale8_f30_{key}.json"
        if not path.exists():
            raise SystemExit(f"missing {path}")
        arms[key] = load_trials(path)

    n = len(next(iter(arms.values())))
    for key, trials in arms.items():
        if len(trials) != n:
            raise SystemExit(f"{key} has {len(trials)} trials, expected {n}")

    # Shared injection check
    mismatches = []
    zero_eff = []
    inj = eff = 0
    ref = arms["prap"]
    for i, t in enumerate(ref):
        k = injection_key(t)
        if t.get("n_effective", 0) == 0:
            zero_eff.append(i)
        inj += int(t.get("n_injected") or 0)
        eff += int(t.get("n_effective") or 0)
        for key, trials in arms.items():
            if injection_key(trials[i]) != k or trials[i].get("seed") != t.get("seed"):
                mismatches.append((i, key, t.get("seed"), trials[i].get("seed")))
    total_slots = n * 8
    print("Eight-agent confirmatory cell (unified seed)")
    print(f"  trials per arm: {n}")
    print(f"  injected: {inj}/{total_slots} ({inj / total_slots:.3f})")
    print(f"  effective: {eff}/{total_slots} ({eff / total_slots:.3f})")
    print(f"  zero-effective trials: {zero_eff}")
    if mismatches:
        print("  INJECTION MISMATCH:", mismatches)
        sys.exit(1)
    print("  injection patterns: identical across five arms")
    print()
    print(f"{'Arm':<16} {'Succ':>8} {'Wilson 95%':>14} {'Waste':>14} {'Tokens':>16} {'Calls':>12}")
    counts = {}
    for key, label in SCALE_ARMS:
        trials = arms[key]
        succ = sum(1 for t in trials if t.get("success"))
        counts[key] = succ
        lo, hi = wilson(succ, n)
        w_m, w_s = mean_sd([float(t.get("wasted_ops") or 0) for t in trials])
        t_m, t_s = mean_sd([float(t.get("tokens") or 0) for t in trials])
        c_m, c_s = mean_sd([float(t.get("llm_calls") or 0) for t in trials])
        err = sum(1 for t in trials if t.get("error"))
        extra = ""
        if key == "prap":
            q_m, q_s = mean_sd([float(t.get("n_quarantined") or 0) for t in trials])
            extra = f"  quarantines {q_m:.2f} ({q_s:.2f})"
        if err:
            extra += f"  errors={err}"
        print(
            f"{label:<16} {succ:>2}/{n:<5} [{lo:.0%}, {hi:.0%}] "
            f"{w_m:6.2f} ({w_s:5.2f}) {t_m:7.0f} ({t_s:5.0f}) {c_m:6.2f} ({c_s:5.2f}){extra}"
        )

    print()
    print("Preregistered Fisher tests (two-sided):")
    tests = [
        ("Write gating vs lease-only", "prap", "leaseonly"),
        ("Write gating vs orchestrator", "prap", "orch"),
        ("Orchestrator vs lease-only", "orch", "leaseonly"),
    ]
    for name, a, b in tests:
        p = fisher_two_sided(counts[a], n, counts[b], n)
        print(f"  {name}: {counts[a]}/{n} vs {counts[b]}/{n}, p = {p:.6f}")
    print("Descriptive only:")
    for name, a, b in (
        ("Write gating vs prompt", "prap", "promptfix"),
        ("Write gating vs no protocol", "prap", "noproto"),
    ):
        p = fisher_two_sided(counts[a], n, counts[b], n)
        print(f"  {name}: {counts[a]}/{n} vs {counts[b]}/{n}, p = {p:.6f}")


def summarize_t2c4() -> None:
    print()
    print("Topology T2/C4 later baselines (same deterministic injection; not a new ranking)")
    print(f"{'Arm':<16} {'Succ':>8} {'Wilson 95%':>14} {'Waste':>14} {'A=Qwen':>10} {'A=DeepSeek':>12}")
    for key, label in T2C4_ARMS:
        path = RUN / f"t2c4_{key}.json"
        if not path.exists():
            print(f"  missing {path}")
            continue
        trials = load_trials(path)
        n = len(trials)
        succ = sum(1 for t in trials if t.get("success"))
        lo, hi = wilson(succ, n)
        w_m, w_s = mean_sd([float(t.get("wasted_ops") or 0) for t in trials])
        split = defaultdict(lambda: [0, 0])
        for t in trials:
            model = (t.get("models") or {}).get("A", "?")
            if "qwen" in str(model).lower():
                tag = "qwen"
            elif "deepseek" in str(model).lower():
                tag = "deepseek"
            else:
                tag = str(model)
            split[tag][1] += 1
            if t.get("success"):
                split[tag][0] += 1
        q = split.get("qwen", [0, 0])
        ds = split.get("deepseek", [0, 0])
        print(
            f"{label:<16} {succ:>2}/{n:<5} [{lo:.0%}, {hi:.0%}] "
            f"{w_m:6.2f} ({w_s:5.2f}) {q[0]:>3}/{q[1]:<6} {ds[0]:>3}/{ds[1]:<6}"
        )


def main() -> None:
    if not RUN.is_dir():
        raise SystemExit(f"missing {RUN}")
    summarize_scale()
    summarize_t2c4()


if __name__ == "__main__":
    main()
