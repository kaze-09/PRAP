"""R2 (PRAP_prereg_R2.md): closed-loop evaluation of a release-time geometric ledger constraint.

Frozen before the first API call. Reuses design_doc.py and run_design_doc.py
helpers; writes only to prototype/constraint_runs/ and never touches
design_doc_rerun/, unified_seed_rerun/ or combo_gate_orch/.

Offline self-test (no API key):
    python run_constraint.py --suite selftest

Small-batch live run:
    python run_constraint.py --suite P --trials 2
    python run_constraint.py --suite all --trials 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from statistics import mean

import run_design_doc as rdd
from design_doc import (
    KEY_BORE, KEY_OUTER, KEY_REV, KEY_STATUS, KEY_WALL,
    DesignAgent, consistent, is_success, is_wrong_release, snapshot,
)
from llm_agent import load_clients
from prap_core import Arbiter, digest
from testbed import ScriptedAgent

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "constraint_runs")
SEED_PREFIX = "constraint-20261112"
CONDS = ("P", "O")
ARMS = ("leaseonly", "gate", "gate_constraint")
TRIALS = 10


def log(msg: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT_DIR, "run.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def seed_for(cond: str, trial: int) -> str:
    return f"{SEED_PREFIX}-{cond}-{trial}"


def path_for(cond: str, arm: str) -> str:
    return os.path.join(OUT_DIR, f"constraint_{cond}_{arm}.json")


def make_sim_for(arm: str) -> object:
    sim = rdd.make_sim(l4_enabled=not arm.startswith("leaseonly"))
    sim.arbiter.dimension_constraint = arm == "gate_constraint"
    return sim


def constraint_denies(sim) -> int:
    return sum(1 for e in sim.arbiter.log
               if e[0] == "op" and e[6] == "dimension_constraint")


def run_one(arm: str, cond: str, trial: int, clients: dict) -> dict:
    seed = seed_for(cond, trial)
    names = rdd.models_for_trial(trial)
    local = {}
    for aid, cname in names.items():
        src = clients[cname]
        local[aid] = type(src)(src.name, src.base_url, src.api_key,
                              src.model, src.temperature, src.timeout)
    plan_slip = cond == "P"
    sim = make_sim_for(arm)
    DesignAgent(
        sim, "A", local["A"], rdd.A_WRITE, inject_bore=False,
        obs_seed=f"{seed}-A",
    )
    DesignAgent(
        sim, "B", local["B"], rdd.B_WRITE, inject_bore=not plan_slip,
        plan_slip=plan_slip, reassign_on_isolate=False,
        obs_seed=f"{seed}-B",
    )
    sim.agents["A"].start(0)
    sim.agents["B"].start(1)
    try:
        sim.run(until=rdd.HORIZON)
        rec = rdd.record_trial(sim, arm, trial, names)
    except Exception as exc:  # noqa: BLE001
        rec = rdd.record_trial(sim, arm, trial, names, error=repr(exc))
        rec["traceback"] = traceback.format_exc()
    rec["suite"] = "r2_constraint"
    rec["seed"] = seed
    rec["condition"] = cond
    rec["dimension_constraint"] = bool(getattr(sim.arbiter, "dimension_constraint", False))
    rec["constraint_denies"] = constraint_denies(sim)
    rec["final_consistent"] = bool(consistent(snapshot(sim.arbiter)))
    return rec


def summarize(path: str) -> None:
    rows = json.loads(open(path, encoding="utf-8").read() or "[]")
    if not rows:
        return
    n = len(rows)
    def rate(key):
        return sum(1 for r in rows if r.get(key)) / n
    def avg(key):
        vals = [r.get(key) or 0 for r in rows]
        return mean(vals) if vals else 0.0
    print(f"{os.path.basename(path)} n={n} "
          f"success={sum(1 for r in rows if r.get('success'))}/{n} "
          f"wrong_release={sum(1 for r in rows if r.get('wrong_release'))}/{n} "
          f"consistent={sum(1 for r in rows if r.get('final_consistent'))}/{n} "
          f"constraint_denies={sum(r.get('constraint_denies') or 0 for r in rows)} "
          f"waste={avg('wasted_ops'):.2f} tokens={avg('tokens'):.0f} "
          f"calls={avg('llm_calls'):.2f} denies={avg('denies'):.2f} "
          f"errors={sum(1 for r in rows if r.get('error'))}", flush=True)


def run_arm(arm: str, cond: str, trials: int, clients: dict, resume: bool) -> str:
    path = path_for(cond, arm)
    done = set()
    if resume and os.path.exists(path):
        done = {r["trial"] for r in json.loads(open(path, encoding="utf-8").read() or "[]")
                if not r.get("error")}
    for t in range(trials):
        if t in done:
            log(f"skip {cond}/{arm} t{t} (already recorded)")
            continue
        t0 = time.time()
        rec = run_one(arm, cond, t, clients)
        rdd.append_json(path, rec)
        log(f"{cond}/{arm} t{t} success={rec['success']} wrong_release={rec['wrong_release']} "
            f"consistent={rec['final_consistent']} cd={rec['constraint_denies']} "
            f"waste={rec['wasted_ops']} tokens={rec['tokens']} "
            f"calls={rec['llm_calls']} reason={rec['fail_reason']} "
            f"({time.time()-t0:.1f}s)")
    summarize(path)
    return path


# ---------- offline self-test (no API key) ----------

def _belief(sim, inject_bore=False):
    return rdd._belief_from_obs(sim, inject_bore)


def selftest() -> int:
    # 0. Full regression of the original design-file behaviours.
    rc = rdd.selftest()
    if rc:
        return rc

    # 1. Constraint ON, healthy release, including an inconsistent
    #    intermediate state (A writes bore=12 before B fixes outer).
    sim = make_sim_for("gate_constraint")
    ScriptedAgent(
        sim, "A", (KEY_BORE,),
        [("set_param", {"key": KEY_BORE, "value": 12})],
        ttl=8, think=0, belief=_belief(sim),
    )
    ScriptedAgent(
        sim, "B", (KEY_WALL, KEY_OUTER, KEY_STATUS, KEY_REV),
        [("set_param", {"key": KEY_WALL, "value": 5}),
         ("set_param", {"key": KEY_OUTER, "value": 22}),
         ("release_revision", {})],
        ttl=8, think=0, belief=_belief(sim),
    )
    sim.agents["A"].start(0)
    sim.agents["B"].start(2)
    sim.run(until=20)
    assert is_success(sim.arbiter), snapshot(sim.arbiter)
    assert constraint_denies(sim) == 0, "healthy release was refused"
    set_param_results = [e[5] for e in sim.arbiter.log
                         if e[0] == "op" and e[3] == "set_param"]
    assert all(set_param_results), "intermediate writes were refused"
    print("selftest R2-1 ok: healthy release passes; intermediate writes not refused")

    # 2. Constraint ON, inconsistent final state: release is refused.
    sim = make_sim_for("gate_constraint")
    ScriptedAgent(
        sim, "A", (KEY_BORE,),
        [("set_param", {"key": KEY_BORE, "value": 12})],
        ttl=8, think=0, belief=_belief(sim),
    )
    ScriptedAgent(
        sim, "B", (KEY_WALL, KEY_OUTER, KEY_STATUS, KEY_REV),
        [("set_param", {"key": KEY_WALL, "value": 5}),
         ("set_param", {"key": KEY_OUTER, "value": 21}),
         ("release_revision", {})],
        ttl=8, think=0, belief=_belief(sim),
    )
    sim.agents["A"].start(0)
    sim.agents["B"].start(2)
    sim.run(until=20)
    s = snapshot(sim.arbiter)
    assert s[KEY_STATUS] == "in_design", s
    assert not consistent(s), s
    assert not is_wrong_release(sim.arbiter)
    assert constraint_denies(sim) >= 1, "inconsistent release was not refused"
    print("selftest R2-2 ok: inconsistent release refused (dimension_constraint)")

    # 3. Constraint OFF keeps the original wrong-release behaviour.
    sim = make_sim_for("gate")
    ScriptedAgent(
        sim, "A", (KEY_BORE,),
        [("set_param", {"key": KEY_BORE, "value": 12})],
        ttl=8, think=0, belief=_belief(sim),
    )
    ScriptedAgent(
        sim, "B", (KEY_WALL, KEY_OUTER, KEY_STATUS, KEY_REV),
        [("set_param", {"key": KEY_WALL, "value": 5}),
         ("set_param", {"key": KEY_OUTER, "value": 21}),
         ("release_revision", {})],
        ttl=8, think=0, belief=_belief(sim),
    )
    sim.agents["A"].start(0)
    sim.agents["B"].start(2)
    sim.run(until=20)
    assert is_wrong_release(sim.arbiter), snapshot(sim.arbiter)
    assert constraint_denies(sim) == 0
    print("selftest R2-3 ok: constraint OFF preserves original wrong release")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=("selftest", "P", "O", "all", "smoke"),
                    default="selftest")
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--arms", nargs="*", default=list(ARMS))
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    if args.suite == "selftest":
        return selftest()

    os.makedirs(OUT_DIR, exist_ok=True)
    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        log(f"missing API keys: {missing}")
        return 2
    log("clients loaded: " + ", ".join(f"{k}={v.model}" for k, v in clients.items()))
    conds = CONDS if args.suite in ("all", "smoke") else (args.suite,)
    trials = 1 if args.suite == "smoke" else args.trials
    log(f"start suite={args.suite} conds={conds} arms={args.arms} trials={trials}")
    try:
        for cond in conds:
            for arm in args.arms:
                run_arm(arm, cond, trials, clients, resume=not args.no_resume)
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        return 1
    log("all requested work finished")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
