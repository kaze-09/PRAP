"""Summarize the R2 ledger-constraint cell (PRAP_prereg_R2.md).

Reads only prototype/constraint_runs/*.json; no API calls.
Writes constraint_runs/summary.txt for the artifact record.
"""
from __future__ import annotations

import json
import os
from statistics import mean

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "constraint_runs")
CONDS = ("P", "O")
ARMS = ("leaseonly", "gate", "gate_constraint")

lines: list[str] = []


def emit(s: str) -> None:
    print(s, flush=True)
    lines.append(s)


def avg(rows, key):
    vals = [r.get(key) or 0 for r in rows]
    return mean(vals) if vals else 0.0


for cond in CONDS:
    emit(f"=== condition {cond} "
         f"(P: plan slip, cache correct; O: +1 mm bore observation bias) ===")
    emit("arm                  n  success  wrong_release  cd  false_reject  "
         "not_released  waste  tokens  calls  denies  errors")
    for arm in ARMS:
        path = os.path.join(OUT, f"constraint_{cond}_{arm}.json")
        if not os.path.exists(path):
            emit(f"{arm:20s}  (missing)")
            continue
        rows = json.load(open(path, encoding="utf-8"))
        n = len(rows)
        success = sum(1 for r in rows if r.get("success"))
        wrong = sum(1 for r in rows if r.get("wrong_release"))
        cd = sum(r.get("constraint_denies") or 0 for r in rows)
        false_reject = sum(1 for r in rows
                           if r.get("params_correct") and r.get("final_consistent")
                           and not r.get("success"))
        not_released = sum(1 for r in rows
                           if (r.get("document") or {}).get("doc:status") != "released")
        errors = sum(1 for r in rows if r.get("error"))
        emit(f"{arm:20s} {n:2d}  {success:3d}/{n:<2d}  {wrong:3d}/{n:<2d}       "
             f"{cd:2d}  {false_reject:4d}         {not_released:3d}/{n:<2d}       "
             f"{avg(rows,'wasted_ops'):5.2f}  {avg(rows,'tokens'):6.0f}  "
             f"{avg(rows,'llm_calls'):5.2f}  {avg(rows,'denies'):6.2f}  {errors:3d}")
    # How often was the constraint actually exercised?
    for arm in ("gate_constraint",):
        path = os.path.join(OUT, f"constraint_{cond}_{arm}.json")
        if os.path.exists(path):
            rows = json.load(open(path, encoding="utf-8"))
            with_cd = [r for r in rows if (r.get("constraint_denies") or 0) > 0]
            no_cd_no_release = [r for r in rows
                                if not (r.get("constraint_denies") or 0)
                                and (r.get("document") or {}).get("doc:status") != "released"]
            emit(f"{arm}: trials with a constraint denial {len(with_cd)}/{len(rows)}; "
                 f"denial-free trials still unreleased {len(no_cd_no_release)}/{len(rows)}")
    emit("")

emit("Note: wrong_release = 0 under the constraint is a definitional result "
     "(a refused release cannot be a wrong release); only the cost columns "
     "waste / tokens / calls / success carry empirical information. "
     "See PRAP_prereg_R2.md section 5.")

with open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
