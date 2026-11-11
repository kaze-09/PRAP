#!/usr/bin/env python3
"""Reprint official confirmatory tables and the later combination arm (no API key)."""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

HERE = Path(__file__).resolve().parent
RUN = HERE / "unified_seed_rerun"
COMBO = HERE / "combo_gate_orch"

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


def first_assignee_t2c4(trial: dict) -> str:
    for h in trial.get("handoffs") or []:
        if h.get("agent"):
            return str(h["agent"])
    for ag in trial.get("agents") or []:
        if ag.get("assigned"):
            return str(ag.get("id") or "?")
    return "?"


def summarize_combo() -> None:
    print()
    print("Later combination: lease + write gate + official reassignment")
    print("  Not a prospectively registered contrast; not a confirmatory success-rate test.")
    if not COMBO.is_dir():
        print(f"  missing {COMBO}")
        return

    path = COMBO / "t2c4_gateorch.json"
    if not path.exists():
        print(f"  missing {path}")
    else:
        trials = load_trials(path)
        n = len(trials)
        succ = sum(1 for t in trials if t.get("success"))
        lo, hi = wilson(succ, n)
        w_m, w_s = mean_sd([float(t.get("wasted_ops") or 0) for t in trials])
        t_m, t_s = mean_sd([float(t.get("tokens") or 0) for t in trials])
        c_m, c_s = mean_sd([float(t.get("llm_calls") or 0) for t in trials])
        r_m, r_s = mean_sd([float(t.get("reassignments") or 0) for t in trials])
        q_m, q_s = mean_sd([float(t.get("n_quarantined") or 0) for t in trials])
        err = sum(1 for t in trials if t.get("error"))
        print()
        print("Topology T2/C4 combination")
        print(
            f"  success {succ}/{n}  Wilson [{lo:.0%}, {hi:.0%}]  "
            f"waste {w_m:.2f} ({w_s:.2f})  tokens {t_m:.0f} ({t_s:.0f})  "
            f"calls {c_m:.2f} ({c_s:.2f})  reassignments {r_m:.2f} ({r_s:.2f})  "
            f"quarantined {q_m:.2f} ({q_s:.2f})"
        )
        if err:
            print(f"  errors={err}")
        first = defaultdict(lambda: [0, 0])
        models = defaultdict(lambda: [0, 0])
        fails = []
        for t in trials:
            who = first_assignee_t2c4(t)
            first[who][1] += 1
            if t.get("success"):
                first[who][0] += 1
            else:
                fails.append((t.get("trial"), t.get("item_final"), who))
            a_model = str((t.get("models") or {}).get("A", "?"))
            if "qwen" in a_model.lower():
                tag = "A=Qwen"
            elif "deepseek" in a_model.lower():
                tag = "A=DeepSeek"
            else:
                tag = a_model
            models[tag][1] += 1
            if t.get("success"):
                models[tag][0] += 1
        for who in sorted(first):
            a, b = first[who]
            print(f"  first assigned to {who}: {a}/{b}")
        for tag in sorted(models):
            a, b = models[tag]
            print(f"  {tag}: {a}/{b}")
        if fails:
            print("  failures:", ", ".join(f"t{t} {item} (first {who})" for t, item, who in fails))

    path = COMBO / "scale8_f30_gateorch.json"
    if not path.exists():
        print(f"  missing {path}")
        return
    trials = load_trials(path)
    n = len(trials)
    succ = sum(1 for t in trials if t.get("success"))
    lo, hi = wilson(succ, n)
    w_m, w_s = mean_sd([float(t.get("wasted_ops") or 0) for t in trials])
    t_m, t_s = mean_sd([float(t.get("tokens") or 0) for t in trials])
    c_m, c_s = mean_sd([float(t.get("llm_calls") or 0) for t in trials])
    r_m, r_s = mean_sd([float(t.get("reassignments") or 0) for t in trials])
    q_m, q_s = mean_sd([float(t.get("n_quarantined") or 0) for t in trials])
    err = sum(1 for t in trials if t.get("error"))
    inj = sum(int(t.get("n_injected") or 0) for t in trials)
    eff = sum(int(t.get("n_effective") or 0) for t in trials)
    zero_eff = [t.get("trial") for t in trials if int(t.get("n_effective") or 0) == 0]
    zero_ok = [t.get("trial") for t in trials if int(t.get("n_effective") or 0) == 0 and t.get("success")]
    no_re = [t for t in trials if int(t.get("reassignments") or 0) == 0]
    with_re = [t for t in trials if int(t.get("reassignments") or 0) > 0]
    delivered = defaultdict(int)
    for t in trials:
        delivered[int(t.get("delivered") or 0)] += 1
    free_only = [float(t.get("wasted_ops_free_only") or 0) for t in trials]
    print()
    print("Eight-agent combination (same unified seed)")
    print(f"  trials: {n}  injected: {inj}/{n * 8}  effective: {eff}/{n * 8}")
    print(f"  zero-effective trials: {zero_eff}  succeeded: {zero_ok}")
    print(
        f"  success {succ}/{n}  Wilson [{lo:.0%}, {hi:.0%}]  "
        f"waste {w_m:.2f} ({w_s:.2f})  tokens {t_m:.0f} ({t_s:.0f})  "
        f"calls {c_m:.2f} ({c_s:.2f})  reassignments {r_m:.2f} ({r_s:.2f})  "
        f"quarantined {q_m:.2f} ({q_s:.2f})"
    )
    if err:
        print(f"  errors={err}")
    if free_only and max(free_only) == 0 and min(free_only) == 0:
        print("  wasted_ops_free_only is 0 on every trial")
    no_s = sum(1 for t in no_re if t.get("success"))
    with_s = sum(1 for t in with_re if t.get("success"))
    no_w = mean_sd([float(t.get("wasted_ops") or 0) for t in no_re]) if no_re else (float("nan"), float("nan"))
    with_w = mean_sd([float(t.get("wasted_ops") or 0) for t in with_re]) if with_re else (float("nan"), float("nan"))
    print(f"  no reassignment: {no_s}/{len(no_re)} success, waste {no_w[0]:.2f}")
    print(f"  with reassignment: {with_s}/{len(with_re)} success, waste {with_w[0]:.2f}")
    d_bits = ", ".join(f"{k} items in {delivered[k]}" for k in sorted(delivered, reverse=True))
    print(f"  delivered: {d_bits}")

    official = RUN / "scale8_f30_prap.json"
    if official.exists():
        ref = load_trials(official)
        mismatches = []
        if len(ref) != n:
            mismatches.append(f"trial count {n} vs official {len(ref)}")
        else:
            for i, t in enumerate(trials):
                if injection_key(t) != injection_key(ref[i]) or t.get("seed") != ref[i].get("seed"):
                    mismatches.append((i, t.get("seed"), ref[i].get("seed")))
        if mismatches:
            print("  INJECTION MISMATCH vs official write gating:", mismatches)
            sys.exit(1)
        print("  injection patterns: identical to the official write-gating arm")
    else:
        print(f"  official write-gating log not found at {official}; skipped injection check")


def main() -> None:
    if not RUN.is_dir():
        raise SystemExit(f"missing {RUN}")
    summarize_scale()
    summarize_t2c4()
    summarize_combo()


if __name__ == "__main__":
    main()
