"""CSCWD confirmatory rerun with a unified injection seed.

Eight-agent cell (scale=8, fault=0.30, n=30):
  Seed is ``unified-{scale}-{fault}-{trial}``. The arm name is not in the
  seed, so all five arms share the same injection set. Results are written
  under ``unified_seed_rerun/`` and do not overwrite the original campaign
  JSON files.

Topology T2/C4 (n=30):
  Injection is already deterministic (always agent B, +1 x). This run fills
  the three missing arms: noproto, leaseonly, promptfix. Existing
  orch / cnp / groupchat / write-gating / OCC+undo files are left untouched.

Usage:
  python run_unified_confirmatory.py --suite inject   # dry-run injections only
  python run_unified_confirmatory.py --suite scale8
  python run_unified_confirmatory.py --suite t2c4
  python run_unified_confirmatory.py --suite all
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime

from comp_baselines import OrchScaleAgent
from llm_agent import LLMAgent, NoProtocolAgent, load_clients
from run_e1_full import TASKS, check_success
from run_e4 import PromptFixAgent
from scale_campaign import (
    AGENT_STARTS,
    DROP,
    SPEC_HORIZON,
    W,
    H,
    cell_name,
    check_all_delivered,
)
from testbed import Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "unified_seed_rerun")
SCALE = 8
FAULT = 0.3
N_TRIALS = 30
SCALE_ARMS = ("prap", "promptfix", "orch", "noproto", "leaseonly")
T2C4_ARMS = ("noproto", "leaseonly", "promptfix")
SEED_PREFIX = "unified"


def log(msg: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT_DIR, "run.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def unified_rng(scale: int, fault: float, trial: int) -> random.Random:
    return random.Random(f"{SEED_PREFIX}-{scale}-{fault}-{trial}")


def injection_plan(scale: int, fault: float, trial: int) -> list[dict]:
    """Same injection set for every arm of a given (scale, fault, trial)."""
    spec_items = {f"apple{i + 1}": (i + 1, 3) for i in range(scale // 2)}
    item_keys = [f"item:{k}" for k in spec_items]
    rng = unified_rng(scale, fault, trial)
    plan = []
    for i in range(scale):
        my_item = item_keys[i % len(item_keys)]
        base = my_item.split(":")[1]
        px, py = spec_items[base]
        injected = bool(fault > 0 and rng.random() < fault)
        override = None
        effective = False
        if injected:
            shifted = (min(px + 1, W - 1), py)
            override = {my_item: f"world@{fmt_pos(shifted)}"}
            effective = shifted != (px, py)
        plan.append({
            "agent": f"A{i}",
            "item": my_item,
            "true_pos": [px, py],
            "injected": injected,
            "effective": effective,
            "override": override,
            "model": "qwen" if (i + trial) % 2 == 0 else "deepseek",
        })
    return plan


def write_injection_plan(trials: int) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    trials_plan = []
    n_inj = n_eff = 0
    zero_eff_trials = []
    for t in range(trials):
        plan = injection_plan(SCALE, FAULT, t)
        n_i = sum(1 for p in plan if p["injected"])
        n_e = sum(1 for p in plan if p["effective"])
        n_inj += n_i
        n_eff += n_e
        if n_e == 0:
            zero_eff_trials.append(t)
        trials_plan.append({
            "trial": t,
            "n_injected": n_i,
            "n_effective": n_e,
            "agents": plan,
        })
    total = trials * SCALE
    summary = {
        "seed_formula": f"{SEED_PREFIX}-{{scale}}-{{fault}}-{{trial}}",
        "scale": SCALE,
        "fault": FAULT,
        "trials": trials,
        "agent_slots": total,
        "injected": n_inj,
        "injected_rate": n_inj / total,
        "effective": n_eff,
        "effective_rate": n_eff / total,
        "expected_effective_rate": FAULT * 6 / 8,
        "zero_effective_trials": zero_eff_trials,
        "note": "All five eight-agent arms share this set. A3 and A7 target apple4; a +1 clamp is a null fault.",
    }
    out = {"summary": summary, "trials": trials_plan}
    path = os.path.join(OUT_DIR, "injection_plan_scale8.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(
        f"injection plan: inj {n_inj}/{total}={n_inj/total:.3f}, "
        f"eff {n_eff}/{total}={n_eff/total:.3f} "
        f"(expected {FAULT * 6 / 8:.3f}), zero-eff trials={zero_eff_trials}"
    )
    log(f"wrote {path}")
    return out


def wasted_ops(arb) -> tuple[int, int]:
    both = sum(1 for e in arb.log if e[0] in ("op", "op_free") and not e[5])
    free_only = sum(1 for e in arb.log if e[0] == "op_free" and not e[5])
    return both, free_only


def agent_details(agents, plan: list[dict], arb) -> list[dict]:
    by_id = {p["agent"]: p for p in plan}
    details = []
    for ag in agents:
        p = by_id.get(ag.id, {})
        details.append({
            "id": ag.id,
            "model": getattr(ag.client, "model", None),
            "item": p.get("item"),
            "injected": p.get("injected"),
            "effective": p.get("effective"),
            "override": p.get("override"),
            "status": arb.agent_status.get(ag.id) if hasattr(arb, "agent_status") else None,
            "last_verdict": getattr(ag, "last_verdict", None),
            "completed_at": getattr(getattr(ag, "report", None), "completed_at", None),
            "blocked_from": getattr(getattr(ag, "report", None), "blocked_from", None),
            "granted_at": getattr(getattr(ag, "report", None), "granted_at", None),
        })
    return details


def save_records(path: str, records: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_records(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_scale_sim(arm: str, trial: int, clients: dict, plan: list[dict]):
    spec_items = {f"apple{i + 1}": (i + 1, 3) for i in range(SCALE // 2)}
    item_keys = [f"item:{k}" for k in spec_items]
    sim = Sim()
    init_world(
        sim.arbiter,
        spec_items,
        {f"A{i}": AGENT_STARTS[i] for i in range(SCALE)},
        width=W,
        height=H,
    )
    if arm == "leaseonly":
        sim.arbiter.l4_enabled = False

    agents = []
    for i, p in enumerate(plan):
        aid = p["agent"]
        client = clients["qwen"] if p["model"] == "qwen" else clients["deepseek"]
        goal = f"把 {p['item']} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。"
        start = AGENT_STARTS[i]
        override = p["override"]
        if arm == "orch":
            ag = OrchScaleAgent(
                sim, aid, client, goal, [p["item"]], start,
                belief_override=override, is_orchestrator=(i == 0),
            )
        elif arm == "noproto":
            ag = NoProtocolAgent(
                sim, aid, client, goal, [p["item"]], start,
                belief_override=override,
            )
        elif arm == "promptfix":
            ag = PromptFixAgent(
                sim, aid, client, goal, [p["item"]], start,
                belief_override=override,
            )
        else:
            ag = LLMAgent(
                sim, aid, client, goal, [p["item"]], start,
                belief_override=override,
            )
        agents.append(ag)

    if arm == "orch":
        m = SCALE // 2
        item_candidates = {k: [f"A{j}", f"A{j + m}"] for j, k in enumerate(item_keys)}
        agents[0].setup_scale([f"A{i}" for i in range(SCALE)], item_candidates)

    for i, ag in enumerate(agents):
        ag.start(i + 1)
    return sim, agents, item_keys


def run_scale_trial(arm: str, trial: int, clients: dict) -> dict:
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    plan = injection_plan(SCALE, FAULT, trial)
    sim, agents, item_keys = build_scale_sim(arm, trial, clients, plan)
    err = None
    try:
        sim.run(SPEC_HORIZON)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log(f"  scale {arm} t{trial} exception: {err}")
    ok, why = check_all_delivered(sim.arbiter, item_keys)
    arb = sim.arbiter
    waste, waste_free = wasted_ops(arb)
    n_quarantined = sum(
        1 for a in getattr(arb, "agent_status", {}).values() if a == "suspect"
    )
    rec = {
        "cell": cell_name(SCALE, FAULT, arm),
        "suite": "scale8_unified",
        "seed": f"{SEED_PREFIX}-{SCALE}-{FAULT}-{trial}",
        "trial": trial,
        "scale": SCALE,
        "fault": FAULT,
        "arm": arm,
        "success": ok,
        "fail_reason": why,
        "error": err,
        "delivered": sum(
            1 for k in item_keys
            if arb.state[k].value == f"world@{fmt_pos(DROP)}"
        ),
        "n_items": len(item_keys),
        "n_quarantined": n_quarantined,
        "wasted_ops": waste,
        "wasted_ops_free_only": waste_free,
        "tokens": sum(
            c.usage["prompt_tokens"] + c.usage["completion_tokens"]
            for c in clients.values()
        ),
        "llm_calls": sum(c.usage["calls"] for c in clients.values()),
        "completion_tick": max((a.report.completed_at or 0) for a in agents),
        "n_injected": sum(1 for p in plan if p["injected"]),
        "n_effective": sum(1 for p in plan if p["effective"]),
        "agents": agent_details(agents, plan, arb),
    }
    if arm == "orch":
        rec["reassignments"] = sum(getattr(a, "reassignments", 0) for a in agents)
    return rec


def run_scale8(clients: dict, trials: int, arms: list[str]) -> None:
    write_injection_plan(trials)
    for arm in arms:
        path = os.path.join(OUT_DIR, f"scale8_f30_{arm}.json")
        records = load_records(path)
        if len(records) >= trials:
            log(f"scale8 {arm}: already has {len(records)} trials, skip")
            continue
        log(f"scale8 {arm}: resume from t{len(records)} -> {trials}, out={path}")
        for t in range(len(records), trials):
            t0 = time.time()
            rec = run_scale_trial(arm, t, clients)
            records.append(rec)
            save_records(path, records)
            dt = time.time() - t0
            log(
                f"  [{rec['cell']} t{t}] success={int(rec['success'])} "
                f"delivered={rec['delivered']}/{rec['n_items']} "
                f"eff={rec['n_effective']} waste={rec['wasted_ops']} "
                f"tokens={rec['tokens']} {dt:.1f}s"
            )
        n_ok = sum(r["success"] for r in records)
        log(f"  scale8 {arm} done: {n_ok}/{len(records)}")


def build_t2c4_sim(arm: str, trial: int, clients: dict):
    spec = TASKS["T2"]
    sim = Sim()
    init_world(sim.arbiter, spec["items"], spec["agents"],
               width=spec["W"], height=spec["H"])
    if arm == "leaseonly":
        sim.arbiter.l4_enabled = False

    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]

    drop = fmt_pos(spec["drop"])
    first_item = list(spec["items"])[0]
    goal = f"把 item:{first_item} 从它所在位置取起，送到 {drop} 并 place。"
    px, py = spec["items"][first_item]
    override_b = {f"item:{first_item}": f"world@({min(px + 1, spec['W'] - 1)},{py})"}

    cls = {
        "noproto": NoProtocolAgent,
        "leaseonly": LLMAgent,
        "promptfix": PromptFixAgent,
    }[arm]
    a = cls(sim, "A", m_a, goal, [f"item:{first_item}"], spec["agents"]["A"])
    b = cls(sim, "B", m_b, goal, [f"item:{first_item}"], spec["agents"]["B"],
            belief_override=override_b)
    a.start(1)
    b.start(2)
    return sim, (a, b)


def run_t2c4_trial(arm: str, trial: int, clients: dict) -> dict:
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, agents = build_t2c4_sim(arm, trial, clients)
    err = None
    try:
        sim.run(TASKS["T2"]["horizon"])
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log(f"  t2c4 {arm} t{trial} exception: {err}")
    arb = sim.arbiter
    ok, why = check_success(sim, "C4", "T2")
    waste, waste_free = wasted_ops(arb)
    lease_agent: dict = {}
    for e in arb.log:
        if e[0] == "grant":
            lease_agent[e[2]] = e[3]
    rec = {
        "suite": "t2c4_missing_baselines",
        "arm": arm,
        "task": "T2",
        "cond": "C4",
        "trial": trial,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok,
        "fail_reason": why,
        "error": err,
        "wasted_ops": waste,
        "wasted_ops_free_only": waste_free,
        "failed_ops": waste,
        "n_quarantined": sum(
            1 for a in getattr(arb, "agent_status", {}).values() if a == "suspect"
        ),
        "grants": arb.stats.get("grants") if hasattr(arb, "stats") else None,
        "denies": arb.stats.get("denies") if hasattr(arb, "stats") else None,
        "expires": arb.stats.get("expires") if hasattr(arb, "stats") else None,
        "tokens": sum(
            ag.client.usage["prompt_tokens"] + ag.client.usage["completion_tokens"]
            for ag in agents
        ),
        "llm_calls": sum(ag.client.usage["calls"] for ag in agents),
        "verdicts": {ag.id: getattr(ag, "last_verdict", None) for ag in agents},
        "status": dict(getattr(arb, "agent_status", {})),
        "item_final": arb.state["item:apple1"].value,
        "op_seq": [
            {
                "kind": e[0],
                "agent": (lease_agent.get(e[2], "?") if e[0] == "op"
                          else e[4].get("agent", "?") if e[0] == "op_free" and isinstance(e[4], dict)
                          else None),
                "op": e[3] if e[0] in ("op", "op_free") else None,
                "ok": e[5] if e[0] in ("op", "op_free") else None,
            }
            for e in arb.log if e[0] in ("op", "op_free", "grant", "deny")
        ],
    }
    return rec


def run_t2c4(clients: dict, trials: int, arms: list[str]) -> None:
    for arm in arms:
        path = os.path.join(OUT_DIR, f"t2c4_{arm}.json")
        records = load_records(path)
        if len(records) >= trials:
            log(f"t2c4 {arm}: already has {len(records)} trials, skip")
            continue
        log(f"t2c4 {arm}: resume from t{len(records)} -> {trials}, out={path}")
        for t in range(len(records), trials):
            t0 = time.time()
            rec = run_t2c4_trial(arm, t, clients)
            records.append(rec)
            save_records(path, records)
            dt = time.time() - t0
            log(
                f"  [t2c4 {arm} t{t}] success={int(rec['success'])} "
                f"waste={rec['wasted_ops']} tokens={rec['tokens']} {dt:.1f}s"
            )
        n_ok = sum(r["success"] for r in records)
        log(f"  t2c4 {arm} done: {n_ok}/{len(records)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["inject", "scale8", "t2c4", "all"], default="all")
    ap.add_argument("--trials", type=int, default=N_TRIALS)
    ap.add_argument("--arms", type=str, default="")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"start suite={args.suite} trials={args.trials}")

    if args.suite in ("inject", "scale8", "all"):
        write_injection_plan(args.trials)
        if args.suite == "inject":
            return 0

    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        log(f"missing API keys: {missing}")
        return 2
    log("clients loaded: " + ", ".join(f"{k}={v.model}" for k, v in clients.items()))

    try:
        if args.suite in ("scale8", "all"):
            arms = [a.strip() for a in args.arms.split(",") if a.strip()] or list(SCALE_ARMS)
            run_scale8(clients, args.trials, arms)
        if args.suite in ("t2c4", "all"):
            arms = [a.strip() for a in args.arms.split(",") if a.strip()] or list(T2C4_ARMS)
            # if user passed scale8 arm names while suite=all, ignore for t2c4
            if args.suite == "all" and args.arms:
                arms = list(T2C4_ARMS)
            run_t2c4(clients, args.trials, arms)
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        return 1

    log("all requested work finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
